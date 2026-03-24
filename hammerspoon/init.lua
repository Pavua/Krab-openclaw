-- Краб / Hammerspoon init.lua
-- Управление окнами macOS + HTTP-сервер для интеграции с userbot
--
-- Установка:
--   1. Установить Hammerspoon (https://www.hammerspoon.org/)
--   2. Скопировать этот файл в ~/.hammerspoon/init.lua
--      или добавить строку: require("path.to.init") в уже существующий init.lua
--   3. Перезагрузить Hammerspoon (⌘R или через меню)
--
-- API (HTTP сервер на порту 10101):
--   POST /window   { "action": "focus|move|resize|tile", ... }
--   GET  /windows  → JSON список открытых окон
--   GET  /status   → {"ok": true, "version": "..."}

local json = require("hs.json")
local log  = hs.logger.new("krab", "info")

-- ─── Константы ──────────────────────────────────────────────────────────────
local KRAB_PORT     = 10101
local KRAB_PASS     = nil           -- опционально: "secret" для базовой защиты

-- ─── Утилиты ────────────────────────────────────────────────────────────────
local function jsonOK(data)
    return json.encode(data), 200, {["Content-Type"] = "application/json"}
end

local function jsonErr(msg, code)
    return json.encode({ok = false, error = msg}), code or 400,
           {["Content-Type"] = "application/json"}
end

-- ─── Вспомогательные: поиск окна по имени приложения ────────────────────────
local function findWindow(appName)
    if not appName or appName == "" then
        return hs.window.focusedWindow()
    end
    local apps = hs.application.find(appName)
    if not apps then return nil, "app_not_found: " .. appName end
    if type(apps) ~= "table" then apps = {apps} end
    for _, app in ipairs(apps) do
        local win = app:mainWindow() or app:focusedWindow()
        if win then return win end
    end
    return nil, "no_window_for_app: " .. appName
end

-- ─── Команды управления окнами ───────────────────────────────────────────────

-- focus: сфокусировать окно приложения
local function cmdFocus(params)
    local win, err = findWindow(params.app)
    if not win then return nil, err or "no_window" end
    win:focus()
    return {ok = true, app = (win:application() and win:application():name()) or "?",
            title = win:title()}
end

-- move: переместить/изменить размер окна (координаты 0..1 от экрана)
local function cmdMove(params)
    local win, err = findWindow(params.app)
    if not win then return nil, err or "no_window" end
    local screen = win:screen():frame()

    -- Нормализуем: если < 2 — считаем за долю экрана, иначе абсолютные пиксели
    local function resolve(v, total)
        if v <= 1.5 then return math.floor(v * total) else return math.floor(v) end
    end

    local x = resolve(params.x or 0, screen.w)
    local y = resolve(params.y or 0, screen.h)
    local w = resolve(params.w or 1, screen.w)
    local h = resolve(params.h or 1, screen.h)
    win:setFrame({x = screen.x + x, y = screen.y + y, w = w, h = h})
    return {ok = true, frame = {x = x, y = y, w = w, h = h}}
end

-- tile: классические раскладки
local TILE_PRESETS = {
    left  = hs.layout.left50,
    right = hs.layout.right50,
    top   = {x = 0,   y = 0,   w = 1, h = 0.5},
    bottom= {x = 0,   y = 0.5, w = 1, h = 0.5},
    full  = hs.layout.maximized,
    max   = hs.layout.maximized,
}

local function cmdTile(params)
    local preset = (params.preset or "left"):lower()
    local unit   = TILE_PRESETS[preset]
    if not unit then
        return nil, "unknown_preset: " .. preset ..
               " (valid: left, right, top, bottom, full)"
    end
    local win, err = findWindow(params.app)
    if not win then return nil, err or "no_window" end
    win:moveToUnit(unit)
    return {ok = true, preset = preset,
            app = (win:application() and win:application():name()) or "?"}
end

-- list: перечислить видимые окна
local function cmdListWindows()
    local result = {}
    for _, win in ipairs(hs.window.visibleWindows()) do
        local appObj = win:application()
        table.insert(result, {
            id    = win:id(),
            title = win:title(),
            app   = appObj and appObj:name() or "?",
        })
    end
    return result
end

-- ─── HTTP-обработчики ────────────────────────────────────────────────────────
local function handleWindow(method, path, headers, body)
    if method ~= "POST" then return jsonErr("method_not_allowed", 405) end

    local params, decodeErr = json.decode(body or "{}")
    if not params then
        return jsonErr("invalid_json: " .. (decodeErr or "?"))
    end

    local action = (params.action or ""):lower()

    if action == "focus" then
        local res, err = cmdFocus(params)
        if err then return jsonErr(err) end
        return jsonOK(res)
    elseif action == "move" or action == "resize" then
        local res, err = cmdMove(params)
        if err then return jsonErr(err) end
        return jsonOK(res)
    elseif action == "tile" then
        local res, err = cmdTile(params)
        if err then return jsonErr(err) end
        return jsonOK(res)
    else
        return jsonErr("unknown_action: " .. action ..
                       " (valid: focus, move, resize, tile)")
    end
end

local function handleWindows(method)
    if method ~= "GET" then return jsonErr("method_not_allowed", 405) end
    return jsonOK({ok = true, windows = cmdListWindows()})
end

local function handleStatus(method)
    if method ~= "GET" then return jsonErr("method_not_allowed", 405) end
    return jsonOK({
        ok      = true,
        version = hs.processInfo.version,
        build   = hs.processInfo.buildVersion,
        screens = #hs.screen.allScreens(),
    })
end

-- ─── Запуск HTTP-сервера ─────────────────────────────────────────────────────
local server = hs.httpserver.new(false, false)
server:setName("krab-hs")
server:setPort(KRAB_PORT)
server:setInterface("localhost")

server:setCallback(function(method, path, headers, body)
    -- Опциональная простая аутентификация через заголовок X-Krab-Pass
    if KRAB_PASS then
        local incoming = headers["X-Krab-Pass"] or headers["x-krab-pass"] or ""
        if incoming ~= KRAB_PASS then
            return jsonErr("unauthorized", 401)
        end
    end

    if path == "/window" then
        return handleWindow(method, path, headers, body)
    elseif path == "/windows" then
        return handleWindows(method)
    elseif path == "/status" then
        return handleStatus(method)
    else
        return jsonErr("not_found: " .. path, 404)
    end
end)

server:start()
log.i(string.format("Krab HTTP server started on localhost:%d", KRAB_PORT))

-- ─── Горячие клавиши (owner только — опционально) ───────────────────────────
-- Раскомментируй если хочешь клавиатурные шорткаты

-- hs.hotkey.bind({"alt", "cmd"}, "Left",  function() cmdTile({preset = "left"})  end)
-- hs.hotkey.bind({"alt", "cmd"}, "Right", function() cmdTile({preset = "right"}) end)
-- hs.hotkey.bind({"alt", "cmd"}, "Up",    function() cmdTile({preset = "full"})  end)
-- hs.hotkey.bind({"alt", "cmd"}, "Down",  function() cmdTile({preset = "bottom"})end)

-- ─── Автоперезагрузка при изменении init.lua ────────────────────────────────
local configWatcher = hs.pathwatcher.new(os.getenv("HOME") .. "/.hammerspoon/", function(files)
    for _, f in ipairs(files) do
        if f:sub(-4) == ".lua" then
            log.i("Reloading Hammerspoon config...")
            hs.reload()
            return
        end
    end
end)
configWatcher:start()

log.i("Krab / Hammerspoon init loaded")
