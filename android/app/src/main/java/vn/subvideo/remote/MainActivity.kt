package vn.subvideo.remote

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.ContentValues
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.net.http.SslCertificate
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.File
import java.io.OutputStream
import java.net.URLDecoder
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import kotlin.concurrent.thread

class MainActivity : ComponentActivity() {
    private lateinit var web: WebView
    private lateinit var root: FrameLayout
    private var base = ""
    private var pendingShare: String? = null
    private var fullView: View? = null
    private var fullCb: WebChromeClient.CustomViewCallback? = null

    private val scan = registerForActivityResult(ScanContract()) { r ->
        val t = r.contents
        if (t != null) handlePair(t) else if (!Store.configured) showSetup()
    }
    private val askNotify = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Store.init(this)
        Notify.channels(this)
        root = FrameLayout(this)
        web = WebView(this)
        web.setBackgroundColor(Color.parseColor("#0B0E14"))
        root.addView(web, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        setContentView(root)
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = false
            allowContentAccess = false
            userAgentString = "$userAgentString SubvideoApp/2.3.0"
        }
        CookieManager.getInstance().setAcceptCookie(true)
        web.addJavascriptInterface(Bridge(), "SubvideoApp")
        web.webViewClient = Client()
        web.webChromeClient = Chrome()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                when {
                    fullView != null -> fullCb?.onCustomViewHidden()
                    web.canGoBack() -> web.goBack()
                    else -> finish()
                }
            }
        })
        if (Build.VERSION.SDK_INT >= 33) askNotify.launch(Manifest.permission.POST_NOTIFICATIONS)
        Notify.schedule(this)
        if (!handleIntent(intent)) connect()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    /** true = đã xử lý (ghép máy / chia sẻ link). */
    private fun handleIntent(i: Intent?): Boolean {
        if (i == null) return false
        val data = i.data
        if (i.action == Intent.ACTION_VIEW && data != null && data.scheme == "subvideo") {
            handlePair(data.toString())
            return true
        }
        if (i.action == Intent.ACTION_SEND && i.type == "text/plain") {
            val t = i.getStringExtra(Intent.EXTRA_TEXT) ?: return false
            pendingShare = t
            if (base.isEmpty()) connect() else deliverShare()
            return true
        }
        return false
    }

    private fun deliverShare() {
        val t = pendingShare ?: return
        pendingShare = null
        web.evaluateJavascript("window.svShared && window.svShared(${JSONObject.quote(t)})", null)
    }

    // ------------------------------------------------------------------ nối máy tính
    private fun connect(hash: String = "") {
        if (!Store.configured) {
            showSetup()
            return
        }
        toast("Đang nối máy tính ${Store.pc}…")
        thread {
            val b = Pin.pickBase()
            runOnUiThread {
                base = b
                val h = hash.ifEmpty { if (Store.token.isNotEmpty()) "#tok=" + Uri.encode(Store.token) else "" }
                web.loadUrl("$b/$h")
            }
        }
    }

    private fun handlePair(text: String) {
        val u = Uri.parse(text.trim())
        if (u.scheme != "subvideo" || u.host != "pair") {
            toast("Đây không phải mã QR ghép máy của Subvideo")
            if (!Store.configured) showSetup()
            return
        }
        val code = u.getQueryParameter("c") ?: ""
        Store.lan = u.getQueryParameter("lan") ?: ""
        Store.lfp = (u.getQueryParameter("lfp") ?: "").lowercase()
        Store.vps = u.getQueryParameter("vps") ?: ""
        Store.vfp = (u.getQueryParameter("vfp") ?: "").lowercase()
        Store.pc = u.getQueryParameter("pc") ?: ""
        Store.token = ""
        val name = Uri.encode("${Build.MANUFACTURER} ${Build.MODEL}".trim())
        connect("#pair=" + Uri.encode(code) + "&name=" + name)
    }

    private fun showSetup() {
        base = ""
        val html = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{margin:0;background:#0B0E14;color:#E8ECF3;font:15px/1.5 system-ui,sans-serif;padding:24px 16px}
.l{width:52px;height:52px;border-radius:14px;background:linear-gradient(135deg,#FF5A4E,#FF8A3D);color:#1A0B08;
font:800 28px system-ui;display:grid;place-items:center}h1{font-size:20px;margin:14px 0 4px}p{color:#C3CAD6}
.c{background:#141925;border:1px solid #252C3A;border-radius:14px;padding:14px;margin-top:14px;display:flex;flex-direction:column;gap:10px}
button{min-height:48px;border-radius:12px;border:0;font:700 15px system-ui;background:linear-gradient(90deg,#FF5A4E,#FF8A3D);color:#1A0B08}
button.s{background:#181E2C;color:#E8ECF3;border:1px solid #343D50}
input{min-height:44px;border-radius:10px;border:1px solid #343D50;background:#1A2030;color:#E8ECF3;padding:0 12px;font-size:15px}
small{color:#8E98AC}</style></head><body><div class="l">S</div><h1>Subvideo – điều khiển tool</h1>
<p>Trên máy tính: mở tool → Cài đặt → <b>Điều khiển từ xa</b> → bật → quét mã QR hiện ra.</p>
<div class="c"><button onclick="SubvideoApp.scanQr()">Quét mã QR trên máy tính</button></div>
<div class="c"><small>Hoặc nhập địa chỉ (dòng "Cùng Wi-Fi nhà") rồi nhập mã 6 số / mật khẩu ở trang tiếp theo</small>
<input id="u" placeholder="https://192.168.1.5:8765" inputmode="url">
<button class="s" onclick="SubvideoApp.manual(document.getElementById('u').value)">Mở</button></div>
</body></html>"""
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
    }

    private fun startScan() {
        val o = ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setBeepEnabled(false)
            .setOrientationLocked(false).setPrompt("Quét mã QR ở Cài đặt → Điều khiển từ xa trên máy tính")
        scan.launch(o)
    }

    private fun openSettings() {
        val items = arrayOf(
            "Quét mã QR mới (ghép lại)",
            "Kết nối: tự chọn (mạng nhà trước, không được thì VPS)",
            "Kết nối: chỉ mạng nhà" + if (Store.lan.isEmpty()) " (chưa có)" else "",
            "Kết nối: chỉ VPS" + if (Store.vps.isEmpty()) " (chưa có)" else "",
            "Quên máy tính này",
        )
        AlertDialog.Builder(this)
            .setTitle("Máy tính: ${Store.pc}\nMạng nhà: ${Store.lan.ifEmpty { "–" }}\nVPS: ${Store.vps.ifEmpty { "–" }}")
            .setItems(items) { _, w ->
                when (w) {
                    0 -> startScan()
                    1, 2, 3 -> {
                        Store.mode = arrayOf("auto", "lan", "vps")[w - 1]
                        connect()
                    }
                    4 -> {
                        Store.clear()
                        CookieManager.getInstance().removeAllCookies(null)
                        showSetup()
                    }
                }
            }.show()
    }

    // ------------------------------------------------------------------ chứng chỉ tự ký: chỉ nhận đúng vân tay đã ghép
    private fun certSha(c: SslCertificate?): String? {
        if (c == null) return null
        val x: X509Certificate? = if (Build.VERSION.SDK_INT >= 29) c.x509Certificate else {
            val der = SslCertificate.saveState(c).getByteArray("x509-certificate") ?: return null
            CertificateFactory.getInstance("X.509").generateCertificate(ByteArrayInputStream(der)) as X509Certificate
        }
        return x?.let { Pin.sha256(it.encoded) }
    }

    inner class Client : WebViewClient() {
        @SuppressLint("WebViewClientOnReceivedSslError")
        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            val url = error.url ?: ""
            val want = Store.pinFor(url)
            val got = certSha(error.certificate)
            if (!want.isNullOrEmpty() && got == want) handler.proceed()
            else if (want == "" && got != null) {
                // địa chỉ nhập tay (chưa có vân tay từ mã QR): hỏi 1 lần, đồng ý thì nhớ vân tay này
                AlertDialog.Builder(this@MainActivity)
                    .setTitle("Tin máy tính này?")
                    .setMessage("Máy tính dùng chứng chỉ tự ký.\nVân tay: ${got.take(16)}…\n" +
                        "So với dòng vân tay ở Cài đặt → Điều khiển từ xa trên máy tính (hoặc quét mã QR cho chắc).")
                    .setPositiveButton("Tin") { _, _ ->
                        Store.setPin(url, got)
                        handler.proceed()
                    }
                    .setNegativeButton("Không") { _, _ -> handler.cancel() }
                    .setOnCancelListener { handler.cancel() }
                    .show()
            } else {
                handler.cancel()
                toast("Chứng chỉ máy chủ không khớp mã đã ghép – quét lại mã QR trên máy tính")
            }
        }

        override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
            val u = req.url
            if (base.isNotEmpty() && u.toString().startsWith(base)) return false
            if (u.scheme == "subvideo") {
                handlePair(u.toString())
                return true
            }
            try {
                startActivity(Intent(Intent.ACTION_VIEW, u))
            } catch (e: Exception) {
            }
            return true
        }

        override fun onPageFinished(view: WebView, url: String) {
            if (base.isNotEmpty() && url.startsWith(base)) deliverShare()
        }

        override fun onReceivedError(view: WebView, req: WebResourceRequest, err: android.webkit.WebResourceError) {
            if (!req.isForMainFrame) return
            val alt = if (base == Store.lan.trimEnd('/')) Store.vps.trimEnd('/') else Store.lan.trimEnd('/')
            val html = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0;background:#0B0E14;color:#E8ECF3;
font:15px/1.5 system-ui;padding:24px 16px}b{color:#FF8A3D}button{min-height:48px;width:100%;border-radius:12px;
border:1px solid #343D50;background:#181E2C;color:#E8ECF3;font:600 15px system-ui;margin-top:10px}</style></head><body>
<b>Không nối được máy tính</b><p>${err.description}</p><p>Tool trên máy tính phải đang mở, đã bật Điều khiển từ xa.
Ở ngoài mạng nhà cần có VPS.</p><button onclick="SubvideoApp.retry('')">Thử lại</button>
${if (alt.isNotEmpty()) "<button onclick=\"SubvideoApp.retry('alt')\">Thử đường khác</button>" else ""}
<button onclick="SubvideoApp.openSettings()">Cài đặt kết nối</button></body></html>"""
            view.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
        }
    }

    inner class Chrome : WebChromeClient() {
        override fun onShowCustomView(view: View, callback: CustomViewCallback) {
            fullView = view
            fullCb = callback
            root.addView(view, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            web.visibility = View.GONE
            window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        }

        override fun onHideCustomView() {
            fullView?.let { root.removeView(it) }
            fullView = null
            fullCb = null
            web.visibility = View.VISIBLE
            window.clearFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        }
    }

    // ------------------------------------------------------------------ cầu nối cho trang (app.js)
    inner class Bridge {
        @JavascriptInterface
        fun notify(title: String, text: String, finished: String) {
            Notify.job(this@MainActivity, title, text)
            seen(finished)
        }

        @JavascriptInterface
        fun seen(finished: String) {
            val t = finished.toDoubleOrNull() ?: return
            if (t > Store.lastEvent) Store.lastEvent = t
        }

        @JavascriptInterface
        fun setToken(origin: String, token: String) {
            Store.token = token
        }

        @JavascriptInterface
        fun scanQr() {
            runOnUiThread { startScan() }
        }

        @JavascriptInterface
        fun openSettings() {
            runOnUiThread { this@MainActivity.openSettings() }
        }

        @JavascriptInterface
        fun retry(which: String) {
            runOnUiThread {
                if (which == "alt") Store.mode = if (base == Store.lan.trimEnd('/')) "vps" else "lan"
                connect()
            }
        }

        @JavascriptInterface
        fun manual(url: String) {
            var u = url.trim().trimEnd('/')
            if (u.isEmpty()) return
            if (!u.startsWith("http")) u = "https://$u"
            runOnUiThread {
                Store.lan = u
                Store.lfp = ""
                Store.vps = ""
                Store.vfp = ""
                Store.pc = Uri.parse(u).host ?: ""
                base = u
                web.loadUrl("$u/")
            }
        }

        @JavascriptInterface
        fun download(url: String, name: String, share: Boolean) {
            thread { fetch(url, name, share) }
        }
    }

    // ------------------------------------------------------------------ tải file về điện thoại (ghim chứng chỉ như trang)
    private fun fetch(url: String, name0: String, share: Boolean) {
        val id = Notify.newId()
        var name = name0
        try {
            val c = Pin.open(url, 20000)
            c.readTimeout = 120000
            CookieManager.getInstance().getCookie(url)?.let { c.setRequestProperty("Cookie", it) }
            if (c.responseCode != 200) {
                val err = try { JSONObject(c.errorStream.bufferedReader().readText()).optString("error") } catch (e: Exception) { "" }
                throw Exception(err.ifEmpty { "Lỗi ${c.responseCode}" })
            }
            if (name.isEmpty()) {
                val cd = c.getHeaderField("Content-Disposition") ?: ""
                name = Regex("filename\\*=UTF-8''([^;]+)").find(cd)?.groupValues?.get(1)?.let { URLDecoder.decode(it, "UTF-8") }
                    ?: Uri.parse(url).lastPathSegment ?: "subvideo.bin"
            }
            val mime = c.contentType?.substringBefore(';') ?: "application/octet-stream"
            val total = c.contentLengthLong
            val (out, uri) = target(name, mime)
            Notify.progress(this, id, name, 0, false)
            var got = 0L
            var lastPct = -1
            c.inputStream.use { inp ->
                out.use { o ->
                    val buf = ByteArray(256 * 1024)
                    while (true) {
                        val n = inp.read(buf)
                        if (n < 0) break
                        o.write(buf, 0, n)
                        got += n
                        val pct = if (total > 0) (got * 100 / total).toInt() else 0
                        if (pct != lastPct && pct % 2 == 0) {
                            lastPct = pct
                            Notify.progress(this, id, name, pct, false)
                        }
                    }
                }
            }
            finishTarget(uri)
            Notify.progress(this, id, name, 100, true, uri, mime)
            runOnUiThread {
                if (share) {
                    val i = Intent(Intent.ACTION_SEND).setType(mime).putExtra(Intent.EXTRA_STREAM, uri)
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    startActivity(Intent.createChooser(i, "Đăng / chia sẻ $name"))
                } else toast("Đã tải về: Download/Subvideo/$name")
            }
        } catch (e: Exception) {
            Notify.progress(this, id, "Tải lỗi: $name", 0, true)
            runOnUiThread { toast("Tải lỗi: ${e.message}") }
        }
    }

    private fun target(name: String, mime: String): Pair<OutputStream, Uri> {
        if (Build.VERSION.SDK_INT >= 29) {
            val v = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, name)
                put(MediaStore.Downloads.MIME_TYPE, mime)
                put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Subvideo")
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v)
                ?: throw Exception("Không tạo được file trong Download")
            return Pair(contentResolver.openOutputStream(uri) ?: throw Exception("Không ghi được file"), uri)
        }
        val dir = File(getExternalFilesDir(null), "Download").apply { mkdirs() }
        val f = File(dir, name)
        return Pair(f.outputStream(), FileProvider.getUriForFile(this, "$packageName.files", f))
    }

    private fun finishTarget(uri: Uri) {
        if (Build.VERSION.SDK_INT >= 29 && uri.scheme == "content" && uri.authority == MediaStore.AUTHORITY) {
            contentResolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
        }
    }

    private fun toast(t: String) = Toast.makeText(this, t, Toast.LENGTH_LONG).show()
}
