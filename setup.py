#!/usr/bin/env python

import os
import sys

from distutils import sysconfig
from setuptools import setup


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
if gl_inc:
    cflags = os.environ.get("CFLAGS")
    cflags = "%s-I%s" % (' ' if cflags else '', gl_inc)
    os.environ['CFLAGS'] = cflags

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

setup(
    setup_requires=['pbr'],
    pbr=True,
)
