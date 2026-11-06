-- SPDX-License-Identifier: Apache-2.0
local realffi = require('ffi')
local state = {}
local updates, opened, closed, releases, settings_releases = 0, 0, 0, 0, 0
local source = {}
local function copy(t)
    local result = {}
    for k,v in pairs(t) do result[k] = v end
    return result
end
local function reset()
    state = {width=3840, height=2160, current_width=1920, current_height=1080,
        driver='sc0710', present=true, readable=true, ioctl_ok=true,
        id='v4l2_input', apply=true,
        settings={device_id='/dev/video0', pixelformat=1448695129,
            resolution=-1, custom='preserve'}}
    updates, opened, closed, releases, settings_releases = 0, 0, 0, 0, 0
    script_update({source_name='Capture', enabled=true})
end
local mockffi = {C={}}
setmetatable(mockffi, {__index=realffi})
mockffi.C.open = function(device, flags)
    assert(device == '/dev/video0' and flags == 526338)
    if not state.readable then return -1 end
    opened = opened + 1
    return 5
end
mockffi.C.ioctl = function(fd, request, arg)
    assert(fd == 5)
    if not state.ioctl_ok then return -1 end
    if request == 0x80685600 then
        realffi.copy(arg[0].driver, state.driver .. '\0')
    elseif request == 0xc0d05604 then
        assert(arg[0].type == 1)
        arg[0].fmt.pix.width = state.width
        arg[0].fmt.pix.height = state.height
    else error('unexpected ioctl') end
    return 0
end
mockffi.C.close = function(fd) assert(fd==5); closed=closed+1; return 0 end
package.loaded.ffi = mockffi
obslua = {
    LOG_INFO=200, LOG_WARNING=300,
    obs_get_source_by_name=function(name)
        assert(name=='Capture'); if state.present then return source end
    end,
    obs_source_get_id=function(s) assert(s==source); return state.id end,
    obs_source_get_settings=function(s) assert(s==source); return copy(state.settings) end,
    obs_data_get_string=function(t,k) return t[k] or '' end,
    obs_data_get_bool=function(t,k) return t[k] or false end,
    obs_data_get_int=function(t,k) return t[k] or 0 end,
    obs_data_set_int=function(t,k,v) t[k]=v end,
    obs_source_get_width=function() return state.current_width end,
    obs_source_get_height=function() return state.current_height end,
    obs_source_update=function(s,t)
        assert(s==source); updates=updates+1; state.settings=copy(t)
        if state.apply then
            state.current_width=state.width; state.current_height=state.height
        end
    end,
    obs_source_release=function(s) assert(s==source); releases=releases+1 end,
    obs_data_release=function() settings_releases=settings_releases+1 end,
    script_log=function() end,
    timer_add=function(fn,ms) assert(fn==recover_format and ms==1000) end,
    timer_remove=function(fn) assert(fn==recover_format) end,
}
dofile(arg[1])
assert(realffi.sizeof('struct quadcap_obs_v4l2_format')==208)
assert(realffi.offsetof('struct quadcap_obs_v4l2_format','fmt')==8)
assert(realffi.sizeof('struct quadcap_obs_v4l2_capability')==104)
local function tick(n) for _=1,n do recover_format() end end

reset()
tick(1); assert(updates==0, 'restart must wait for a stable format')
tick(1); assert(updates==1, '1080p client must reopen for a 4K source')
assert(state.settings.resolution==3840*4294967296+2160)
assert(state.settings.custom=='preserve' and state.settings.pixelformat==1448695129)
assert(opened==closed and releases==2 and settings_releases==2)
tick(8); assert(updates==1, 'matching capture must not restart repeatedly')

reset(); state.current_width=3840; state.current_height=2160
tick(3); assert(updates==0, 'unchanged signal size must not restart capture')
reset(); state.width=1280; state.height=720
tick(2); assert(updates==1, 'a smaller source must also renegotiate')
reset(); state.width=2560; state.height=1440; tick(1)
state.width=3840; state.height=2160; tick(1)
assert(updates==0, 'transient formats must not restart capture')
tick(1); assert(updates==1)
reset(); state.readable=false; tick(3)
assert(updates==0 and opened==closed and releases==3)
reset(); state.ioctl_ok=false; tick(3)
assert(updates==0 and opened==closed)
reset(); state.driver='uvcvideo'; tick(3)
assert(updates==0 and opened==closed, 'unrelated cameras must not be changed')
reset(); state.width=0; tick(3); assert(updates==0)
reset(); state.width=9000; tick(3); assert(updates==0)
reset(); state.present=false; tick(3); assert(updates==0 and opened==0)
reset(); state.id='image_source'; tick(3)
assert(updates==0 and opened==0 and releases==3)
reset(); state.apply=false; tick(20)
assert(updates==1, 'failed recovery must not create a restart loop')
reset(); state.settings.resolution=3840*4294967296+2160; tick(20)
assert(updates==0, 'identical requested settings cannot force a reopen')
reset(); script_update({source_name='Capture', enabled=false}); tick(3)
assert(updates==0 and opened==0)
reset(); script_load({source_name='Capture', enabled=true}); script_unload()
print('PASS: format recovery, debounce, resource cleanup, unrelated-device guards, and lifecycle')
