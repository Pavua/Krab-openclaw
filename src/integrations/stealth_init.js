// stealth_init.js — shared browser stealth layer (Chado §1 P1)
// Инжектируется через add_init_script() / evaluateOnNewDocument()
// Все патчи завёрнуты в try/catch чтобы один сбой не ломал остальные.

(() => {
  // --- 1. navigator.webdriver ---
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  } catch (_) {}

  // --- 2. plugins / languages ---
  try {
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['es-ES', 'es', 'en'] });
  } catch (_) {}

  // --- 3. window.chrome ---
  try {
    if (!window.chrome) window.chrome = { runtime: {} };
  } catch (_) {}

  // --- 4. permissions.query ---
  try {
    const _origQuery = window.navigator.permissions.query.bind(navigator.permissions);
    window.navigator.permissions.query = (params) =>
      params.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : _origQuery(params);
  } catch (_) {}

  // --- 5. Canvas fingerprint noise ---
  try {
    const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function (type, quality) {
      const ctx = this.getContext('2d');
      if (ctx) {
        // Добавляем субпиксельный шум — незаметен визуально, меняет hash
        const imgData = ctx.getImageData(0, 0, this.width || 1, this.height || 1);
        imgData.data[0] = imgData.data[0] ^ 1;
        ctx.putImageData(imgData, 0, 0);
      }
      return _origToDataURL.call(this, type, quality);
    };
  } catch (_) {}

  // --- 6. WebGL vendor / renderer spoof ---
  try {
    const _origGetParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (param) {
      if (param === 37445) return 'Intel Inc.';                  // UNMASKED_VENDOR_WEBGL
      if (param === 37446) return 'Intel Iris OpenGL Engine';    // UNMASKED_RENDERER_WEBGL
      return _origGetParam.call(this, param);
    };
  } catch (_) {}

  try {
    const _origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function (param) {
      if (param === 37445) return 'Intel Inc.';
      if (param === 37446) return 'Intel Iris OpenGL Engine';
      return _origGetParam2.call(this, param);
    };
  } catch (_) {}

  // --- 7. WebRTC local IP leak block ---
  try {
    if (typeof RTCPeerConnection !== 'undefined') {
      const _origCreateOffer = RTCPeerConnection.prototype.createOffer;
      RTCPeerConnection.prototype.createOffer = function (...args) {
        // Блокируем только если нет обработчика (leak-проверки) — иначе noop
        return _origCreateOffer.apply(this, args).then((offer) => {
          // Вырезаем candidate строки с локальными адресами из SDP
          if (offer && offer.sdp) {
            offer = new RTCSessionDescription({
              type: offer.type,
              sdp: offer.sdp.replace(
                /a=candidate:[^\r\n]*(?:host|srflx)[^\r\n]*(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)[^\r\n]*/g,
                ''
              ),
            });
          }
          return offer;
        });
      };
    }
  } catch (_) {}
})();
