import hashlib
import re


def web_templates(binary, assets):
    assert (
        hashlib.sha256(binary).hexdigest()
        == "c89bda10432a42754ffa514a57dc41a848472c6a14b32112ac410fd622320789"
    )
    result = {}
    fields = {
        "header.conf": (0x2FEFBA, []),
        "rtmp.conf": (0x2FF03C, ["@RTMP_BIND@", "@RTMP_PORT@"] + ["@HTTP_PORT@"] * 7),
        "http.conf": (
            0x2FF3CC,
            [
                "@RTSP_PORT@",
                "@HTTP_PORT@",
                "@HTTP_FLAG@",
                "@HTTP_REMOTE_FLAG@",
                "@ONVIF_PORT@",
            ],
        ),
        "https.conf": (
            0x2FFCFA,
            ["@HTTPS_PORT@", "@TLS_CERT@", "@TLS_KEY@", "@ONVIF_PORT@"],
        ),
    }
    for name, (address, values) in fields.items():
        offset = address - 0x10000
        text = binary[offset : binary.index(b"\0", offset)].decode()
        assert len(re.findall(r"%[sd]", text)) == len(values)
        replacements = iter(values)
        result[name] = re.sub(r"%[sd]", lambda _: next(replacements), text).encode()
    marker = b"        listen @HTTPS_PORT@ ssl;\n"
    assert result["https.conf"].count(marker) == 1
    result["https.conf"] = result["https.conf"].replace(
        marker, marker + (assets / "settings/nginx-locations.conf").read_bytes()
    )
    return result
