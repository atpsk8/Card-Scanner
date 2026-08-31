"""Local Wi-Fi photo intake: lets a phone browser upload card photos straight
into this computer's phone-incoming folder while Card Desk is running.

No internet access, no cloud service. The server does three things: check a
short session code, serve the phone page (with a live-camera, hands-free
capture mode that falls back to manual tap-to-shoot when the camera is not
available), and save uploaded image files. Importing those files into the
collection (OCR, catalog lookup, etc.) still goes through the exact same
reviewed pipeline as a manual folder import — see App.poll_phone_inbox in ui.py.

The server speaks HTTPS with a self-signed certificate generated once and
cached under the store's data folder. This is required by phones' browsers:
live camera access (getUserMedia) is only allowed on a "secure context",
which plain http:// on a LAN IP does not qualify as. The phone will show a
one-time certificate warning; the user has to tap through it (see the hint
text shown next to the link in Card Desk).
"""
import datetime
import hmac
import http.server
import io
import ipaddress
import json
import queue
import secrets
import socket
import ssl
import threading
import uuid

EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # matches scanning.MAX_BYTES
DEFAULT_PORT = 8420

PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Card Desk phone scan</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif; background: #F3F5F6; color: #142D39; padding: 18px 16px 28px; text-align: center; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  p.sub { color: #657985; font-size: 13px; margin: 0 0 14px; min-height: 32px; }
  #camWrap.hidden, #fileWrap.hidden { display: none; }
  #stage { position: relative; max-width: 460px; margin: 0 auto 12px; border-radius: 16px; overflow: hidden; background: #000; aspect-ratio: 3 / 4; }
  #video { width: 100%; height: 100%; object-fit: cover; display: block; }
  #flash { position: absolute; inset: 0; background: #fff; opacity: 0; pointer-events: none; }
  #flash.on { opacity: 0.85; }
  #stageHint { position: absolute; left: 0; right: 0; bottom: 0; padding: 10px; font-size: 13px; color: #fff; background: linear-gradient(transparent, rgba(0,0,0,.65)); }
  .row { display: flex; gap: 10px; justify-content: center; max-width: 460px; margin: 0 auto 12px; }
  button.ctl { flex: 1; font: inherit; font-size: 15px; font-weight: 600; padding: 14px 10px; border-radius: 12px; border: 1px solid #D5DEE1; background: #fff; color: #142D39; }
  button.ctl.primary { background: #147D73; color: #fff; border-color: #147D73; }
  label.shoot { display: block; max-width: 460px; margin: 0 auto 16px; background: #147D73; color: #fff; font-size: 20px; font-weight: 600; padding: 32px 12px; border-radius: 16px; user-select: none; }
  label.shoot.busy { background: #7B9994; }
  input[type=file] { display: none; }
  #status { min-height: 20px; font-size: 14px; margin-bottom: 6px; }
  #count { color: #657985; font-size: 13px; }
  .err { color: #B3261E; }
</style>
</head>
<body>
  <h1>Card Desk &mdash; phone scan</h1>
  <p class="sub" id="subtitle">Starting camera&hellip;</p>

  <div id="camWrap" class="hidden">
    <div id="stage">
      <video id="video" autoplay playsinline muted></video>
      <div id="flash"></div>
      <div id="stageHint">Place a card in frame and hold still</div>
    </div>
    <div class="row">
      <button class="ctl" id="pauseBtn" type="button">Pause auto-capture</button>
      <button class="ctl primary" id="captureBtn" type="button">Capture now</button>
    </div>
  </div>

  <div id="fileWrap" class="hidden">
    <label class="shoot" id="shootLabel" for="file">Tap to photograph a card</label>
    <input type="file" id="file" accept="image/*" capture="environment">
  </div>

  <div id="status">Ready.</div>
  <div id="count">Uploaded this session: 0</div>

  <canvas id="workCanvas" width="64" height="64" style="display:none"></canvas>
  <canvas id="shotCanvas" style="display:none"></canvas>

<script>
(function () {
  var key = ##KEY##;
  var uploaded = 0;
  var statusEl = document.getElementById('status');
  var countEl = document.getElementById('count');
  var subtitle = document.getElementById('subtitle');

  function setStatus(text, isErr) {
    statusEl.className = isErr ? 'err' : '';
    statusEl.textContent = text;
  }

  function uploadBlob(blob, label) {
    setStatus('Uploading ' + label + '…');
    return fetch('/upload?key=' + encodeURIComponent(key), {
      method: 'POST',
      headers: { 'Content-Type': blob.type || 'image/jpeg' },
      body: blob
    }).then(function (response) {
      if (!response.ok) { throw new Error('Server rejected the photo (' + response.status + ')'); }
      uploaded += 1;
      countEl.textContent = 'Uploaded this session: ' + uploaded;
      setStatus('Uploaded. Ready for the next card.');
    }).catch(function (err) {
      setStatus('Upload failed: ' + err.message + '. Try again.', true);
    });
  }

  // ---- Fallback: manual tap-to-shoot, used when a live camera stream isn't available ----
  function startFileFallback(reason) {
    document.getElementById('camWrap').classList.add('hidden');
    document.getElementById('fileWrap').classList.remove('hidden');
    subtitle.textContent = reason;
    var input = document.getElementById('file');
    var shootLabel = document.getElementById('shootLabel');
    input.addEventListener('change', function () {
      var file = input.files[0];
      if (!file) return;
      shootLabel.classList.add('busy');
      shootLabel.textContent = 'Uploading…';
      uploadBlob(file, file.name).finally(function () {
        shootLabel.classList.remove('busy');
        shootLabel.textContent = 'Tap to photograph a card';
        input.value = '';
      });
    });
  }

  // ---- Preferred: live camera, auto-capture when motion settles (card placed on a stand) ----
  function startHandsFreeCamera(stream) {
    document.getElementById('fileWrap').classList.add('hidden');
    document.getElementById('camWrap').classList.remove('hidden');
    subtitle.textContent = 'Hands-free: put a card in frame and hold still. Swap cards as each one finishes.';
    var video = document.getElementById('video');
    video.srcObject = stream;
    var work = document.getElementById('workCanvas');
    var workCtx = work.getContext('2d', { willReadFrequently: true });
    var shot = document.getElementById('shotCanvas');
    var flash = document.getElementById('flash');
    var stageHint = document.getElementById('stageHint');
    var pauseBtn = document.getElementById('pauseBtn');
    var captureBtn = document.getElementById('captureBtn');

    var STILL_THRESHOLD = 5;      // avg per-pixel gray difference (0-255) counted as "not moving"
    var MOTION_THRESHOLD = 12;    // avg per-pixel gray difference counted as "a card is being swapped"
    var STILL_FRAMES_NEEDED = 8;  // consecutive still samples required before auto-capturing
    var state = 'waiting_for_motion'; // -> 'settling' once motion is seen, back to waiting after each capture
    var stillFrames = 0;
    var lastGray = null;
    var paused = false;
    var busy = false;

    function setHint(text) { stageHint.textContent = text; }

    function grabGray() {
      workCtx.drawImage(video, 0, 0, work.width, work.height);
      var data = workCtx.getImageData(0, 0, work.width, work.height).data;
      var gray = new Uint8ClampedArray(work.width * work.height);
      for (var i = 0, p = 0; i < data.length; i += 4, p++) {
        gray[p] = data[i] * 0.3 + data[i + 1] * 0.59 + data[i + 2] * 0.11;
      }
      return gray;
    }

    function meanDiff(a, b) {
      var total = 0;
      for (var i = 0; i < a.length; i++) { total += Math.abs(a[i] - b[i]); }
      return total / a.length;
    }

    function capture() {
      if (busy) return;
      busy = true;
      shot.width = video.videoWidth;
      shot.height = video.videoHeight;
      shot.getContext('2d').drawImage(video, 0, 0);
      flash.classList.add('on');
      setTimeout(function () { flash.classList.remove('on'); }, 150);
      setHint('Captured. Swap in the next card.');
      shot.toBlob(function (blob) {
        uploadBlob(blob, 'card').finally(function () { busy = false; });
      }, 'image/jpeg', 0.92);
    }

    function tick() {
      if (!paused && !busy && video.videoWidth > 0) {
        var gray = grabGray();
        if (lastGray) {
          var d = meanDiff(lastGray, gray);
          if (state === 'waiting_for_motion') {
            if (d > MOTION_THRESHOLD) {
              state = 'settling';
              stillFrames = 0;
              setHint('Hold still…');
            }
          } else if (state === 'settling') {
            if (d < STILL_THRESHOLD) {
              stillFrames += 1;
              if (stillFrames >= STILL_FRAMES_NEEDED) {
                capture();
                state = 'waiting_for_motion';
                stillFrames = 0;
              }
            } else {
              stillFrames = 0;
            }
          }
        }
        lastGray = gray;
      }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);

    pauseBtn.addEventListener('click', function () {
      paused = !paused;
      pauseBtn.textContent = paused ? 'Resume auto-capture' : 'Pause auto-capture';
      setHint(paused ? 'Auto-capture paused. Use Capture now.' : 'Place a card in frame and hold still');
    });
    captureBtn.addEventListener('click', capture);

    setStatus('Camera ready.');
  }

  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false })
      .then(startHandsFreeCamera)
      .catch(function (err) {
        startFileFallback('Camera unavailable (' + err.name + '). Tap to photograph each card instead.');
      });
  } else {
    startFileFallback('This browser can’t use the live camera here. Tap to photograph each card instead.');
  }
})();
</script>
</body>
</html>
"""


def make_qr_image(data, box_size=4, border=4):
    """Render `data` (the phone-upload URL) as a QR code PIL Image, so scanning
    it with a phone camera opens and authenticates the page with no typing."""
    import qrcode
    qr = qrcode.QRCode(border=border, box_size=box_size)
    qr.add_data(data)
    qr.make(fit=True)
    buffer = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    buffer.seek(0)
    from PIL import Image
    return Image.open(buffer).convert("RGB")


def local_ip():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))  # no packet actually sent; just picks the outbound interface
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def get_or_create_certificate(store):
    """Return (cert_path, key_path) for a self-signed TLS certificate, generating
    and caching one under the store's data folder the first time it's needed.
    Phones will still show a one-time trust warning for it — that is expected
    for a certificate this computer made for itself rather than a public CA."""
    cert_path = store.root / "phone-cert.pem"
    key_path = store.root / "phone-key.pem"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Card Desk (local device)")])
    alt_names = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    try:
        alt_names.append(x509.IPAddress(ipaddress.ip_address(local_ip())))
    except ValueError:
        pass
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    return cert_path, key_path


def make_handler(intake):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Card Desk shows its own status; keep the console quiet

        def _authorized(self):
            from urllib.parse import urlparse, parse_qs
            supplied = parse_qs(urlparse(self.path).query).get("key", [""])[0]
            return hmac.compare_digest(supplied, intake.token)

        def _reply(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split("?", 1)[0] != "/":
                self._reply(404, "text/plain; charset=utf-8", b"Not found.")
            elif not self._authorized():
                self._reply(403, "text/plain; charset=utf-8", b"Missing or incorrect code. Open the link shown on the Scan & import page in Card Desk.")
            else:
                page = PAGE_TEMPLATE.replace("##KEY##", json.dumps(intake.token))
                self._reply(200, "text/html; charset=utf-8", page.encode("utf-8"))

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/upload" or not self._authorized():
                self._reply(403, "text/plain; charset=utf-8", b"Forbidden.")
                return
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            extension = EXTENSION_BY_CONTENT_TYPE.get(content_type)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if not extension:
                self._reply(400, "text/plain; charset=utf-8", b"Unsupported photo type.")
            elif length <= 0 or length > MAX_UPLOAD_BYTES:
                self._reply(400, "text/plain; charset=utf-8", b"Photo is missing or exceeds the 25 MB limit.")
            else:
                data = self.rfile.read(length)
                path = intake.incoming / (uuid.uuid4().hex + extension)
                path.write_bytes(data)
                intake.inbox.put(path)
                self._reply(200, "application/json", b'{"ok": true}')

    return Handler


class PhoneIntake:
    """Owns the local upload server. Created once per Store; started and
    stopped explicitly from the UI so nothing listens on the network unless
    the user asked for it."""

    def __init__(self, store):
        self.store = store
        self.incoming = store.root / "phone-incoming"
        self.incoming.mkdir(exist_ok=True)
        self.inbox = queue.Queue()
        self.httpd = None
        self.thread = None
        self.token = ""

    @property
    def running(self):
        return self.httpd is not None

    def start(self, port=DEFAULT_PORT):
        self.token = f"{secrets.randbelow(1_000_000):06d}"
        handler = make_handler(self)
        try:
            httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
        except OSError:
            httpd = http.server.ThreadingHTTPServer(("0.0.0.0", 0), handler)
        cert_path, key_path = get_or_create_certificate(self.store)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        self.httpd = httpd
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return f"https://{local_ip()}:{self.httpd.server_port}/?key={self.token}"

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.httpd = None
        self.thread = None
