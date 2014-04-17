import os
import sys

try:
    import setuptools
    setup = setuptools.setup
    Extension = setuptools.Extension
except ImportError:
    from distutils import core
    setup = core.setup
    Extension = core.Extension

from distutils import sysconfig


def _greenlet_include_dir():
    """Find the greenlet include directory.  It may be different than
    the python include directory that the extension builder adds
    automatically.  If it is the same, return None.  Otherwise, return
    the path or raise.
    """
    py_inc = sysconfig.get_python_inc()
    greenlet_header = os.path.join(py_inc, 'greenlet', 'greenlet.h')
    if os.path.exists(greenlet_header):
        return
    # Next best guess is it's in /usr/local/include/<python>/
    path_parts = os.path.split(py_inc)
    # if 'py_inc' ends in a /, then the last part of the split is '', so
    # look at the previous one
    python_part = path_parts[-1] if path_parts[-1] else path_parts[-2]
    # We're assuming unix here...
    gl_hdr_dir = '/usr/local/include/%s' % python_part
    if os.path.exists(gl_hdr_dir + '/greenlet/greenlet.h'):
        return gl_hdr_dir
    raise RuntimeError('Not sure where the greenlet header is')


gl_inc = _greenlet_include_dir()
ext_incs = []
if gl_inc:
    ext_incs.append(gl_inc)

sources = ['cext.c', 'fil_lock.c', 'fil_cond.c', 'filament.c',
           'fil_message.c', 'fil_scheduler.c', 'fil_semaphore.c',
           'fil_util.c', 'fil_waiter.c', 'fil_exceptions.c',
           'fil_iothread.c', 'fil_io.c', 'fil_timer.c']
source_dir = 'src'
include_dir = 'include'

if '--debug' in sys.argv:
    orig_opt = sysconfig.get_config_var('OPT')
    opt = '-g'
    if orig_opt:
        for x in orig_opt.split(' '):
            if x.startswith('-W'):
                opt += ' ' + x
    os.environ['OPT'] = opt
else:
    if not os.environ.get('OPT'):
        orig_opt = sysconfig.get_config_var('OPT')
        opt = ''
        if orig_opt:
            got_o = False
            for x in orig_opt.split(' '):
                if x == '-g':
                    continue
                if x.startswith('-O'):
                    got_o = True
                opt += '%s%s' % (' ' if opt else '', x)
            if not got_o:
                opt += '%s-O2' % (' ' if opt else '')
        else:
            opt = '-O2 -DNDEBUG'
        os.environ['OPT'] = opt

#

sources = [os.path.join(source_dir, source) for source in sources]
include_dirs = [include_dir] + ext_incs

cext_mod = Extension('filament._cext',
                     include_dirs=include_dirs,
                     libraries=['pthread', 'event_pthreads'],
                     sources=sources)

setup(name='filament',
      version = '0.0.1',
      description = 'Filament: Microthreads for Python',
      ext_modules = [cext_mod],
      author='Chris Behrens',
      author_email='cbehrens@codestud.com',
      packages=['filament'],
      test_suite='tests',
      license="MIT License",
      classifiers=[
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Programming Language :: C',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.5',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
#        'Programming Language :: Python :: 3',
#        'Programming Language :: Python :: 3.0',
#        'Programming Language :: Python :: 3.1',
#        'Programming Language :: Python :: 3.2',
        'Operating System :: OS Independent',
        'Topic :: Software Development :: Libraries :: Python Modules'],
      )
