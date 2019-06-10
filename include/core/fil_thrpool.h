/*
 * The MIT License (MIT): http://opensource.org/licenses/mit-license.php
 *
 * Copyright (c) 2019, Chris Behrens
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 *
 */

#ifndef __CORE_FIL_THRPOOL_H__
#define __CORE_FIL_THRPOOL_H__

#include "core/filament.h"

#define FIL_THRPOOL_DEFAULT_MIN_THREADS   10
#define FIL_THRPOOL_DEFAULT_MAX_THREADS   20
#define FIL_THRPOOL_DEFAULT_STACK_SIZE    (256 * 1024)

#define FIL_THRPOOL_CALLBACK_FLAGS_SHUTDOWN   0x00000001
typedef void (*FilThrPoolCallback)(void *thread_state, void *cb_arg, uint32_t flags);
#define FIL_THRPOOL_THR_INIT_FAILURE_RESULT ((void *)-1)
typedef void *(*FilThrPoolInitThrCallback)(void *arg);
/* The arg returned by InitThrCallback will be passed to the Deinit call */
typedef void (*FilThrPoolDeinitThrCallback)(void *thread_state);
typedef void (*FilThrPoolShutdownCallback)(void *thread_state, void *cb_arg);

typedef struct _fil_thr_pool_opt
{
    uint32_t min_thr;
    uint32_t max_thr;
    size_t stack_size;

    FilThrPoolInitThrCallback thr_init_cb;
    void *thr_init_cb_arg;
    FilThrPoolDeinitThrCallback thr_deinit_cb;
} FilThrPoolOpt;

typedef struct _fil_thr_pool_cb_info FilThrPoolCBInfo;

typedef struct _fil_thr_pool_cb_info
{
    FilThrPoolCallback callback;
    void *callback_arg;

    FilThrPoolCBInfo *next;
} FilThrPoolCBInfo;

typedef struct _fil_thr_pool
{
    FilThrPoolOpt opt;

#define FIL_THRPOOL_FLAGS_SHUTDOWN        0x00000001
#define FIL_THRPOOL_FLAGS_SHUTDOWN_NOW    0x00000002
    uint32_t flags;
    uint32_t num_threads;
    uint32_t num_threads_pending;

    pthread_mutex_t lock;
    pthread_cond_t cond;
    pthread_cond_t shutdown_cond;

    FilThrPoolCBInfo *first;
    FilThrPoolCBInfo *last;
} FilThrPool;

static void _fil_thr_pool_thread(FilThrPool *tpool)
{
    FilThrPoolCBInfo *entry;
    void *thread_state = NULL;

    if (tpool->opt.thr_init_cb != NULL)
    {
        thread_state = tpool->opt.thr_init_cb(tpool->opt.thr_init_cb_arg);
        if (thread_state == FIL_THRPOOL_THR_INIT_FAILURE_RESULT)
        {
            /* ruh roh */
            pthread_mutex_lock(&(tpool->lock));
            goto out;
        }
    }

    pthread_mutex_lock(&(tpool->lock));

    for(;;)
    {
        if (tpool->flags & FIL_THRPOOL_FLAGS_SHUTDOWN_NOW)
        {
            goto out;
        }
        while((entry = tpool->first) == NULL)
        {
            if ((tpool->flags & FIL_THRPOOL_FLAGS_SHUTDOWN)
                    || (tpool->num_threads > tpool->opt.max_thr))
            {
                goto out;
            }
            pthread_cond_wait(&(tpool->cond), &(tpool->lock));
        }

        if ((tpool->first = entry->next) == NULL)
        {
            tpool->last = NULL;
        }

        pthread_mutex_unlock(&(tpool->lock));
        entry->callback(thread_state, entry->callback_arg, 0);
        free(entry);
        pthread_mutex_lock(&(tpool->lock));
    }
out:
    /*
     * decrement early but keep a pending shutdown count
     * so that tpool doesn't get freed by a shutdown
     * before we're done. need to decrement early so that
     * the max_thr check above doesn't try to exit
     * too many threads..
     */
    tpool->num_threads--;
    tpool->num_threads_pending++;

    pthread_mutex_unlock(&(tpool->lock));

    if (tpool->opt.thr_deinit_cb != NULL)
    {
        tpool->opt.thr_deinit_cb(thread_state);
    }

    pthread_mutex_lock(&(tpool->lock));
    tpool->num_threads_pending--;
    if (tpool->flags & FIL_THRPOOL_FLAGS_SHUTDOWN &&
            tpool->num_threads == 0 &&
            tpool->num_threads_pending == 0)
    {
        pthread_cond_signal(&(tpool->shutdown_cond));
    }
    pthread_mutex_unlock(&(tpool->lock));
    return;
}

/* must be called with lock and tpool will be freed upon return */
static inline void _fil_thrpool_shutdown(FilThrPool *tpool, int now, void *thread_state, int use_ts)
{
    tpool->flags |= FIL_THRPOOL_FLAGS_SHUTDOWN;
    if (now)
    {
        tpool->flags |= FIL_THRPOOL_FLAGS_SHUTDOWN_NOW;
    }

    while(tpool->num_threads > 0 || tpool->num_threads_pending > 0)
    {
        pthread_cond_broadcast(&(tpool->cond));
        pthread_cond_wait(&(tpool->shutdown_cond), &(tpool->lock));
    }

    pthread_mutex_unlock(&(tpool->lock));

    if (tpool->first != NULL)
    {
        FilThrPoolCBInfo *info;

        /*
         * kinda ugly, but we need to pretend we're running
         * in one of the thread pool threads.
         * If use_ts is true, we've already got it from the
         * thread_state from the caller.
         */
        if (!use_ts && tpool->opt.thr_init_cb != NULL)
        {
            thread_state = tpool->opt.thr_init_cb(tpool->opt.thr_init_cb_arg);
            /*
             * this might result in a failure, but we need to carry on
             */
        }
        while((info = tpool->first) != NULL)
        {
            tpool->first = info->next;
            info->callback(thread_state, info->callback_arg, FIL_THRPOOL_CALLBACK_FLAGS_SHUTDOWN);
            free(info);
        }
        if (!use_ts && tpool->opt.thr_deinit_cb != NULL)
        {
            tpool->opt.thr_deinit_cb(thread_state);
        }
    }

    pthread_mutex_destroy(&(tpool->lock));
    pthread_cond_destroy(&(tpool->cond));
    pthread_cond_destroy(&(tpool->shutdown_cond));
    free(tpool);
}

struct _ftp_shutdown_async_info
{
    FilThrPool *tpool;
    int now;
    FilThrPoolShutdownCallback cb;
    void *cb_arg;
};

static void _fil_thrpool_shutdown_async_thr(struct _ftp_shutdown_async_info *info)
{
    FilThrPool *tpool = info->tpool;
    void *thread_state = NULL;

    if (tpool->opt.thr_init_cb != NULL)
    {
        thread_state = tpool->opt.thr_init_cb(tpool->opt.thr_init_cb_arg);
        /* might result in a failure, but we need to carry on */
    }

    pthread_mutex_lock(&(tpool->lock));
    _fil_thrpool_shutdown(tpool, info->now, thread_state, 1);

    if (info->cb != NULL)
    {
        info->cb(thread_state, info->cb_arg);
    }

    if (tpool->opt.thr_deinit_cb != NULL)
    {
        tpool->opt.thr_deinit_cb(thread_state);
    }

    free(info);
    return;
}

/* must be called with lock */
static inline int _fil_create_min_threads(FilThrPool *tpool)
{
    pthread_attr_t thr_attr;
    int err = 0;
    pthread_t tid;

    if (tpool->num_threads >= tpool->opt.min_thr)
    {
        return err;
    }

    pthread_attr_init(&thr_attr);
    pthread_attr_setscope(&thr_attr, PTHREAD_SCOPE_SYSTEM);
    pthread_attr_setdetachstate(&thr_attr, PTHREAD_CREATE_DETACHED);
    pthread_attr_setstacksize(&thr_attr, tpool->opt.stack_size);

    while(tpool->num_threads < tpool->opt.min_thr)
    {
        tpool->num_threads++;
        if ((err = pthread_create(&tid, &thr_attr, (void *(*)(void *))_fil_thr_pool_thread, tpool)) != 0)
        {
            tpool->num_threads--;
            break;
        }
    }

    pthread_attr_destroy(&thr_attr);
    return err;
}

static inline void fil_thrpool_opt_init(FilThrPoolOpt *opt)
{
    opt->min_thr = FIL_THRPOOL_DEFAULT_MIN_THREADS;
    opt->max_thr = FIL_THRPOOL_DEFAULT_MAX_THREADS;
    opt->stack_size = FIL_THRPOOL_DEFAULT_STACK_SIZE;
    opt->thr_init_cb = NULL;
    opt->thr_init_cb_arg = NULL;
    opt->thr_deinit_cb = NULL;
}

static inline FilThrPool *fil_thrpool_create(FilThrPoolOpt *opt)
{
    int err;

    FilThrPool *tpool = calloc(1, sizeof(FilThrPool));
    if (tpool == NULL)
    {
        errno = ENOMEM;
        return NULL;
    }

    memcpy(&(tpool->opt), opt, sizeof(*opt));
    pthread_mutex_init(&(tpool->lock), NULL);
    pthread_cond_init(&(tpool->cond), NULL);
    pthread_cond_init(&(tpool->shutdown_cond), NULL);

    pthread_mutex_lock(&(tpool->lock));

    if ((err = _fil_create_min_threads(tpool)) != 0)
    {
        _fil_thrpool_shutdown(tpool, 1, NULL, 0);
        errno = err;
        return NULL;
    }

    pthread_mutex_unlock(&(tpool->lock));

    return tpool;
}

static inline void fil_thrpool_set_min_threads(FilThrPool *tpool, uint32_t min_thr)
{
    pthread_mutex_lock(&(tpool->lock));
    tpool->opt.min_thr = min_thr;
    _fil_create_min_threads(tpool);
    pthread_mutex_unlock(&(tpool->lock));
}

static inline void fil_thrpool_set_max_threads(FilThrPool *tpool, uint32_t max_thr)
{
    pthread_mutex_lock(&(tpool->lock));
    tpool->opt.max_thr = max_thr;
    pthread_cond_broadcast(&(tpool->cond));
    pthread_mutex_unlock(&(tpool->lock));
}

static inline int fil_thrpool_run(FilThrPool *tpool, FilThrPoolCallback cb, void *cb_arg)
{
    FilThrPoolCBInfo *cbinfo = malloc(sizeof(FilThrPoolCBInfo));
    if (cbinfo == NULL)
    {
        return -1;
    }

    cbinfo->callback = cb;
    cbinfo->callback_arg = cb_arg;
    cbinfo->next = NULL;

    pthread_mutex_lock(&(tpool->lock));
    if (tpool->flags & FIL_THRPOOL_FLAGS_SHUTDOWN)
    {
        pthread_mutex_unlock(&(tpool->lock));
        return -2;
    }

    if (tpool->first == NULL)
    {
        tpool->first = tpool->last = cbinfo;
    }
    else
    {
        tpool->last->next = cbinfo;
        tpool->last = cbinfo;
    }
    pthread_cond_signal(&(tpool->cond));
    pthread_mutex_unlock(&(tpool->lock));

    return 0;
}

/* this can block the caller, filaments or not! */
static inline void fil_thrpool_shutdown(FilThrPool *tpool, int now)
{
    _fil_thrpool_shutdown(tpool, now, NULL, 0);
}

static inline int fil_thrpool_shutdown_async(FilThrPool *tpool, int now, FilThrPoolShutdownCallback cb, void *cb_arg)
{
    struct _ftp_shutdown_async_info *info;
    pthread_attr_t thr_attr;
    pthread_t tid;
    int err;

    if (tpool == NULL)
    {
        return -EINVAL;
    }

    info = malloc(sizeof(*info));
    if (info == NULL)
    {
        return -ENOMEM;
    }

    info->tpool = tpool;
    info->now = now;
    info->cb = cb;
    info->cb_arg = cb_arg;

    pthread_attr_init(&thr_attr);
    pthread_attr_setscope(&thr_attr, PTHREAD_SCOPE_SYSTEM);
    pthread_attr_setdetachstate(&thr_attr, PTHREAD_CREATE_DETACHED);
    pthread_attr_setstacksize(&thr_attr, tpool->opt.stack_size);

    err = pthread_create(&tid, &thr_attr, (void *(*)(void *))_fil_thrpool_shutdown_async_thr, info);
    if (err)
    {
        free(info);
    }
    pthread_attr_destroy(&thr_attr);
    return -err;
}

#endif /* __CORE_FIL_THRPOOL_H__ */
