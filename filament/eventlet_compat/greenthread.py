import filament


class GreenThread(filament.Filament):
    pass


spawn = filament.spawn
spawn_n = filament.spawn
sleep = filament.sleep


def cancel(gt, *throw_args):
    raise NotImplemented()


def kill(gt, *throw_args):
    raise NotImplemented()


def spawn_after(seconds, func, *args, **kwargs):
    raise NotImplemented()


def spawn_after_local(seconds, func, *args, **kwargs):
    raise NotImplemented()
