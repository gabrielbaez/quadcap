#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the OBS helper with LuaJIT FFI and mocked OBS/device operations."""
import ctypes
import ctypes.util
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
library = ctypes.util.find_library('luajit-5.1')
if library is None:
    raise SystemExit('LuaJIT 5.1 shared library is required for the OBS script test')
lib = ctypes.CDLL(library)
lib.luaL_newstate.restype = ctypes.c_void_p
lib.luaL_openlibs.argtypes = [ctypes.c_void_p]
lib.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
lib.lua_tolstring.restype = ctypes.c_char_p
lib.lua_close.argtypes = [ctypes.c_void_p]
state = lib.luaL_newstate()
assert state
try:
    lib.luaL_openlibs(state)
    helper = str(root / 'packaging/obs/sc0710-format-recovery.lua')
    harness = str(root / 'tests/obs/format_recovery.lua')
    code = f"arg={{[1]={helper!r}}}; dofile({harness!r})".encode()
    status = lib.luaL_loadstring(state, code)
    if status == 0:
        status = lib.lua_pcall(state, 0, 0, 0)
    if status:
        raise SystemExit(lib.lua_tolstring(state, -1, None).decode())
finally:
    lib.lua_close(state)

# Check the handwritten FFI layout and ioctl values against the host kernel ABI.
unit = r'''
#include <assert.h>
#include <stddef.h>
#include <time.h>
#include <linux/videodev2.h>
int main(void) {
    assert(sizeof(struct v4l2_format) == 208);
    assert(offsetof(struct v4l2_format, fmt) == 8);
    assert(sizeof(struct v4l2_capability) == 104);
    assert(VIDIOC_QUERYCAP == 0x80685600UL);
    assert(VIDIOC_G_FMT == 0xc0d05604UL);
}
'''
with tempfile.TemporaryDirectory(prefix='sc0710-obs-abi-') as temp:
    source = Path(temp) / 'abi.c'
    binary = Path(temp) / 'abi'
    source.write_text(unit)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(source),
                    '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('PASS: V4L2 ABI matches host kernel headers')
