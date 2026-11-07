package vn.subvideo.remote

import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/** Máy tính đã ghép: địa chỉ mạng nhà (lan) + vân tay chứng chỉ (lfp), trạm VPS (vps) + vân tay (vfp), thẻ đăng nhập. */
object Store {
    private lateinit var sp: SharedPreferences

    fun init(ctx: Context) {
        if (!::sp.isInitialized) sp = ctx.applicationContext.getSharedPreferences("subvideo", Context.MODE_PRIVATE)
    }

    var lan: String
        get() = sp.getString("lan", "") ?: ""
        set(v) = sp.edit().putString("lan", v).apply()
    var lfp: String
        get() = sp.getString("lfp", "") ?: ""
        set(v) = sp.edit().putString("lfp", v).apply()
    var vps: String
        get() = sp.getString("vps", "") ?: ""
        set(v) = sp.edit().putString("vps", v).apply()
    var vfp: String
        get() = sp.getString("vfp", "") ?: ""
        set(v) = sp.edit().putString("vfp", v).apply()
    var pc: String
        get() = sp.getString("pc", "") ?: ""
        set(v) = sp.edit().putString("pc", v).apply()
    var token: String
        get() = sp.getString("token", "") ?: ""
        set(v) = sp.edit().putString("token", v).apply()
    /** auto / lan / vps */
    var mode: String
        get() = sp.getString("mode", "auto") ?: "auto"
        set(v) = sp.edit().putString("mode", v).apply()
    /** Giờ (theo máy tính) của việc đã báo gần nhất – không báo trùng. */
    var lastEvent: Double
        get() = sp.getString("last_event", "0")?.toDoubleOrNull() ?: 0.0
        set(v) = sp.edit().putString("last_event", v.toString()).apply()

    val configured: Boolean get() = lan.isNotEmpty() || vps.isNotEmpty()

    fun clear() = sp.edit().clear().apply()

    /** Vân tay phải khớp cho máy chủ này ("" = chứng chỉ thật, kiểm như thường; null = máy lạ – không tin). */
    fun pinFor(url: String): String? {
        val host = hostPort(url) ?: return null
        if (lan.isNotEmpty() && hostPort(lan) == host) return lfp
        if (vps.isNotEmpty() && hostPort(vps) == host) return vfp
        return null
    }

    fun setPin(url: String, fp: String) {
        val host = hostPort(url) ?: return
        if (lan.isNotEmpty() && hostPort(lan) == host) lfp = fp
        else if (vps.isNotEmpty() && hostPort(vps) == host) vfp = fp
    }

    private fun hostPort(url: String): String? = try {
        val u = Uri.parse(url)
        val port = if (u.port > 0) u.port else if (u.scheme == "https") 443 else 80
        "${u.host?.lowercase()}:$port"
    } catch (e: Exception) {
        null
    }
}

object Pin {
    fun sha256(der: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it) }

    /** Mở kết nối tới máy tính: https tự ký -> chỉ nhận đúng chứng chỉ có vân tay đã ghép. */
    fun open(url: String, timeoutMs: Int = 15000): HttpURLConnection {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = timeoutMs
        c.readTimeout = timeoutMs.coerceAtLeast(30000)
        val fp = Store.pinFor(url)
        if (c is HttpsURLConnection && !fp.isNullOrEmpty()) {
            val tm = object : X509TrustManager {
                override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
                override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                    if (chain.isEmpty() || sha256(chain[0].encoded) != fp)
                        throw java.security.cert.CertificateException("Vân tay chứng chỉ không khớp")
                }
                override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
            }
            val ctx = SSLContext.getInstance("TLS")
            ctx.init(null, arrayOf<TrustManager>(tm), null)
            c.sslSocketFactory = ctx.socketFactory
            c.hostnameVerifier = HostnameVerifier { _, _ -> true }   // đã ghim đúng chứng chỉ -> tên máy không cần khớp
        }
        if (Store.token.isNotEmpty()) c.setRequestProperty("Authorization", "Bearer " + Store.token)
        return c
    }

    fun ping(base: String, timeoutMs: Int = 2500): Boolean = try {
        val c = open("$base/api/ping", timeoutMs)
        c.readTimeout = timeoutMs
        val ok = c.responseCode == 200
        c.disconnect()
        ok
    } catch (e: Exception) {
        false
    }

    /** Chọn địa chỉ: cùng Wi-Fi (thử nhanh) -> mạng nhà, không thì VPS. */
    fun pickBase(): String {
        val lan = Store.lan.trimEnd('/')
        val vps = Store.vps.trimEnd('/')
        return when (Store.mode) {
            "lan" -> lan.ifEmpty { vps }
            "vps" -> vps.ifEmpty { lan }
            else -> if (lan.isNotEmpty() && (vps.isEmpty() || ping(lan))) lan else vps.ifEmpty { lan }
        }
    }
}
