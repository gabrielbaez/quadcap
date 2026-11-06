-- SPDX-License-Identifier: Apache-2.0
-- Reopen an OBS V4L2 source when sc0710 reports a different frame geometry.
local obs = obslua
local ffi = require('ffi')

-- Linux's 64-bit V4L2 ABI. No external command or host executable is needed,
-- which also allows the helper to run inside the OBS Flatpak sandbox.
ffi.cdef[[
struct quadcap_obs_v4l2_format {
    uint32_t type;
    uint32_t padding;
    union {
        struct { uint32_t width, height; } pix;
        uint8_t raw[200];
        uint64_t alignment;
    } fmt;
};
struct quadcap_obs_v4l2_capability {
    uint8_t driver[16], card[32], bus_info[32];
    uint32_t version, capabilities, device_caps, reserved[3];
};
int open(const char *path, int flags, ...);
int ioctl(int fd, unsigned long request, ...);
int close(int fd);
]]

local source_name = ''
local pending = ''
local stable_ticks = 0
local attempted = false
local enabled = true
local warned = false

local function reset()
    pending = ''
    stable_ticks = 0
    attempted = false
end

local function geometry(device)
    -- The request layout below is specific to the 64-bit Linux V4L2 ABI.
    if not ffi.abi('64bit') or ffi.sizeof('struct quadcap_obs_v4l2_format') ~= 208
        or ffi.sizeof('struct quadcap_obs_v4l2_capability') ~= 104 then
        return nil
    end
    local fd = ffi.C.open(device, 2 + 2048 + 524288) -- RDWR | NONBLOCK | CLOEXEC
    if fd < 0 then return nil end
    local cap = ffi.new('struct quadcap_obs_v4l2_capability[1]')
    local format = ffi.new('struct quadcap_obs_v4l2_format[1]')
    format[0].type = 1 -- V4L2_BUF_TYPE_VIDEO_CAPTURE
    local valid = ffi.C.ioctl(fd, 0x80685600, cap) == 0 -- VIDIOC_QUERYCAP
        and ffi.string(cap[0].driver) == 'sc0710'
    if valid then valid = ffi.C.ioctl(fd, 0xc0d05604, format) == 0 end -- VIDIOC_G_FMT
    ffi.C.close(fd)
    if not valid then return nil end
    local width = tonumber(format[0].fmt.pix.width)
    local height = tonumber(format[0].fmt.pix.height)
    if width < 16 or height < 16 or width > 8192 or height > 8192 then return nil end
    return width, height
end

function recover_format()
    if not enabled or source_name == '' then return end
    local source = obs.obs_get_source_by_name(source_name)
    if source == nil then reset(); return end
    if obs.obs_source_get_id(source) ~= 'v4l2_input' then
        obs.obs_source_release(source)
        reset()
        return
    end
    local settings = obs.obs_source_get_settings(source)
    local device = obs.obs_data_get_string(settings, 'device_id')
    local width, height = geometry(device)
    if width == nil then
        if not warned then
            obs.script_log(obs.LOG_WARNING, 'Cannot query sc0710 format for ' .. device
                .. '; no source changes were made.')
            warned = true
        end
        reset()
    elseif obs.obs_source_get_width(source) == width
        and obs.obs_source_get_height(source) == height then
        warned = false
        reset()
    else
        warned = false
        local key = device .. ':' .. width .. 'x' .. height
        if pending ~= key then
            reset()
            pending = key
        end
        stable_ticks = stable_ticks + 1
        -- Debounce mode negotiation. Restart once per stable format change;
        -- repeatedly applying identical settings cannot restart linux-v4l2.
        if stable_ticks >= 2 and not attempted then
            attempted = true
            local packed = width * 4294967296 + height
            if obs.obs_data_get_int(settings, 'resolution') ~= packed then
                obs.obs_data_set_int(settings, 'resolution', packed)
                obs.obs_source_update(source, settings)
                obs.script_log(obs.LOG_INFO, 'Reopening ' .. source_name .. ' at '
                    .. width .. 'x' .. height .. ' after a capture format change.')
            else
                -- The requested format already matches: forcing the same
                -- settings would not restart linux-v4l2. Leave it untouched.
                obs.script_log(obs.LOG_WARNING, 'Capture settings already request '
                    .. width .. 'x' .. height .. '; automatic format recovery skipped.')
            end
        end
    end
    obs.obs_data_release(settings)
    obs.obs_source_release(source)
end

function script_description()
    return 'Automatically reopen a selected sc0710 V4L2 source when its frame size changes. '
        .. 'For Linux 64-bit OBS, including Flatpak. Select the Elgato source below. '
        .. 'A format change briefly interrupts this video source. '
        .. 'Disable the helper to stop automatic resolution updates.'
end

function script_defaults(settings)
    obs.obs_data_set_default_bool(settings, 'enabled', true)
end

function script_properties()
    local props = obs.obs_properties_create()
    obs.obs_properties_add_bool(props, 'enabled', 'Enable format recovery')
    local list = obs.obs_properties_add_list(props, 'source_name', 'Elgato V4L2 source',
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
    obs.obs_property_list_add_string(list, '(Select a source)', '')
    local sources = obs.obs_enum_sources()
    if sources ~= nil then
        for _, source in ipairs(sources) do
            if obs.obs_source_get_id(source) == 'v4l2_input' then
                local name = obs.obs_source_get_name(source)
                obs.obs_property_list_add_string(list, name, name)
            end
        end
        obs.source_list_release(sources)
    end
    return props
end

function script_update(settings)
    source_name = obs.obs_data_get_string(settings, 'source_name')
    enabled = obs.obs_data_get_bool(settings, 'enabled')
    warned = false
    reset()
end

function script_load(settings)
    script_update(settings)
    obs.timer_add(recover_format, 1000)
end

function script_unload()
    obs.timer_remove(recover_format)
end
