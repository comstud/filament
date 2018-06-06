#!/usr/bin/env python

import os
import sys

from distutils import sysconfig
import setuptools


def _greenlet_header_exists(hdr_dir):
    return os.path.exists(os.path.join(hdr_dir, 'greenlet.h'))


def _greenlet_include_dir():
    """Find the greenlet include directory.  It may be different than
    the python include directory that the extension builder adds
    automatically.  If it is the same, return None.  Otherwise, return
    the path or raise.
    """
    py_inc = sysconfig.get_python_inc()

    def _get_python_part():
        path_parts = os.path.split(py_inc)
        # if 'py_inc' ends in a /, then the last part of the split is '', so
        # look at the previous one
        python_part = path_parts[-1] if path_parts[-1] else path_parts[-2]
        # We might have something like 'python3.5m', so drop last char while
        # it's not a digit
        while python_part and not python_part[-1].isdigit():
            python_part = python_part[:-1]
        return python_part

    def _search_dir(path):
        for x in range(2):
            if _greenlet_header_exists(path):
                return path
            path = os.path.join(path, 'greenlet')

    # Check venv first.
    venv = os.environ.get('VIRTUAL_ENV')
    if venv:
        gl_hdr_dir = os.path.join(venv, 'include', 'site',
                                  _get_python_part())
        for x in range(2):
            if _greenlet_header_exists(gl_hdr_dir):
                return gl_hdr_dir
            gl_hdr_dir = os.path.join(gl_hdr_dir, 'greenlet')

    gl_hdr_dir = _search_dir(py_inc)
    if gl_hdr_dir:
        if gl_hdr_dir == py_inc:
            return
        return gl_hdr_dir

    # Next best guess is it's in /usr/local/include/<python>/
    # We're assuming unix here...
    gl_hdr_dir = os.path.join('/usr/local/include', _get_python_part())
    if _greenlet_header_exists(gl_hdr_dir):
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

setuptools.setup(
    setup_requires=['pbr'],
    pbr=True,
)
