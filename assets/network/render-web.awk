# Render local web settings from the vendor service XML.
# BusyBox awk is the runtime interpreter.

function attribute(s, name, fallback,    value) {
    if (!match(s, "[[:space:]]" name "=\"[^\"]*\"")) {
        return fallback
    }
    value = substr(s, RSTART, RLENGTH)
    sub(/^[^=]*="/, "", value)
    sub(/"$/, "", value)
    return value
}

function port(value) {
    return value ~ /^[0-9]+$/ && value + 0 > 0 && value + 0 < 65536
}

function boolean(value) {
    return value == "0" || value == "1"
}

function readtemplate(name,    text, line, saved) {
    saved = RS
    RS = "\n"
    text = ""
    while ((getline line < (assets "/" name)) > 0) {
        text = text line "\n"
    }
    close(assets "/" name)
    RS = saved
    if (text == "") {
        print "Missing web template: " name > "/dev/stderr"
        exit 1
    }
    return text
}

BEGIN {
    RS = ">"
    http = 80
    https = 443
    rtmp = 1935
    rtsp = 554
    onvif = 8000
    enable_http = 1
    enable_https = 1
    enable_rtmp = 0
    custom_certificate = 0
    certificate = "/mnt/app/nginx_conf/self.crt"
    key = "/mnt/app/nginx_conf/self.key"
}

/<web[[:space:]]/ {
    found = 1
    http = attribute($0, "port", http)
    https = attribute($0, "https_port", https)
    rtmp = attribute($0, "rtmp_port", rtmp)
    enable_http = attribute($0, "enable_http", enable_http)
    enable_https = attribute($0, "enable_https", enable_https)
    enable_rtmp = attribute($0, "enable_rtmp", enable_rtmp)
}

/<rtsp[[:space:]]/ {
    rtsp = attribute($0, "port", rtsp)
}

/<onvif[[:space:]]/ {
    onvif = attribute($0, "port", onvif)
}

/<certificate[[:space:]]/ {
    custom_certificate = attribute($0, "benable", 0)
    if (custom_certificate == 1) {
        certificate = "/mnt/para/" attribute($0, "crt_file", "")
        key = "/mnt/para/" attribute($0, "key_file", "")
    }
}

END {
    if (!found || !port(http) || !port(https) || !port(rtmp) ||
        !port(rtsp) || !port(onvif) || !boolean(enable_http) ||
        !boolean(enable_https) || !boolean(enable_rtmp) ||
        certificate !~ /^\/[A-Za-z0-9_.\/-]+\.crt$/ ||
        key !~ /^\/[A-Za-z0-9_.\/-]+\.key$/) {
        print "Invalid local web configuration" > "/dev/stderr"
        exit 1
    }

    # Explicit branches are compatible with the target BusyBox awk.
    if (enable_http == 1) {
        flag = 111
    } else if (enable_https == 1) {
        flag = 101
    } else {
        flag = 100
    }

    text = readtemplate("header.conf") readtemplate("rtmp.conf") readtemplate("http.conf")
    if (enable_https == 1) {
        text = text readtemplate("https.conf")
    }
    text = text "}\n"

    sub(/include[[:space:]]+mime[.]types;/, "include /mnt/tmp/mime.types;", text)
    gsub(/@HTTP_PORT@/, http, text)
    gsub(/@HTTPS_PORT@/, https, text)
    gsub(/@RTMP_PORT@/, rtmp, text)
    gsub(/@RTSP_PORT@/, rtsp, text)
    gsub(/@ONVIF_PORT@/, onvif, text)
    gsub(/@RTMP_BIND@/, enable_rtmp == 1 ? "" : "127.0.0.1:", text)
    gsub(/@HTTP_FLAG@/, flag, text)
    gsub(/@HTTP_REMOTE_FLAG@/, flag * 10 + 1, text)
    gsub(/@TLS_CERT@/, certificate, text)
    gsub(/@TLS_KEY@/, key, text)
    printf "%s", text
}
