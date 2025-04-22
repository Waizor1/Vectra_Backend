from fastapi import APIRouter, HTTPException
from httpx import AsyncClient

from bloobcat.db.users import Users
from bloobcat.routes.marzban.client import MarzbanClient

router = APIRouter(prefix="/tv")
requests = AsyncClient()

marzban = MarzbanClient()


def param(string, par):
    return string.split(par + "=")[1].split("&")[0]


def get_outbound(r: str, tag: str):
    address = r.split("@")[1].split(":")[0]
    return {
        "mux": {
            "concurrency": -1,
            "enabled": False,
            "xudpConcurrency": 8,
            "xudpProxyUDP443": "",
        },
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": address,
                    "port": int(r.split(address + ":")[1].split("?")[0]),
                    "users": [
                        {
                            "encryption": "none",
                            "flow": "",
                            "id": r.split("vless://")[1].split("@")[0],
                            "level": 8,
                        }
                    ],
                }
            ]
        },
        "streamSettings": {
            "network": "tcp",
            "realitySettings": {
                "fingerprint": param(r, "fp"),
                "publicKey": param(r, "pbk"),
                "serverName": param(r, "sni"),
                "allowInsecure": False,
                "show": False,
            },
            "security": "reality",
            "tcpSettings": {"header": {"type": "none"}},
        },
        "tag": tag,
    }


@router.get("/{tv_code}")
async def tv_code_(tv_code: str):
    user = await Users.get(tv_connect=tv_code)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    url = await marzban.get_url(user)
    configs_ = await requests.get(url + "/info")
    configs = configs_.json()["links"]
    return [
        {
            "dns": {
                "hosts": {
                    "domain:googleapis.cn": "googleapis.com",
                    "dot.pub": ["1.12.12.12", "120.53.53.53"],
                    "dns.alidns.com": [
                        "223.5.5.5",
                        "223.6.6.6",
                        "2400:3200::1",
                        "2400:3200:baba::1",
                    ],
                    "one.one.one.one": [
                        "1.1.1.1",
                        "1.0.0.1",
                        "2606:4700:4700::1111",
                        "2606:4700:4700::1001",
                    ],
                    "dns.google": [
                        "8.8.8.8",
                        "8.8.4.4",
                        "2001:4860:4860::8888",
                        "2001:4860:4860::8844",
                    ],
                    "dns.quad9.net": [
                        "9.9.9.9",
                        "149.112.112.112",
                        "2620:fe::fe",
                        "2620:fe::9",
                    ],
                    "common.dot.dns.yandex.net": [
                        "77.88.8.8",
                        "77.88.8.1",
                        "2a02:6b8::feed:0ff",
                        "2a02:6b8:0:1::feed:0ff",
                    ],
                },
                "servers": ["1.1.1.1"],
            },
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "listen": "127.0.0.1",
                    "port": 10808,
                    "protocol": "socks",
                    "settings": {
                        "auth": "noauth",
                        "udp": True,
                        "userLevel": 8,
                    },
                    "sniffing": {
                        "destOverride": ["http", "tls"],
                        "enabled": True,
                        "routeOnly": False,
                    },
                    "tag": "socks",
                },
                {
                    "listen": "127.0.0.1",
                    "port": 10809,
                    "protocol": "http",
                    "settings": {"userLevel": 8},
                    "tag": "http",
                },
            ],
            "routing": {
                "domainStrategy": "IPIfNonMatch",
                "rules": [
                    {
                        "ip": ["1.1.1.1"],
                        "outboundTag": "proxy",
                        "port": "53",
                        "type": "field",
                    },
                    {
                        "ip": ["223.5.5.5"],
                        "outboundTag": "direct",
                        "port": "53",
                        "type": "field",
                    },
                ],
            },
            "outbounds": [
                get_outbound(configs[0], tag="proxy"),
                {
                    "protocol": "freedom",
                    "settings": {"domainStrategy": "UseIP"},
                    "tag": "direct",
                },
                {
                    "protocol": "blackhole",
                    "settings": {"response": {"type": "http"}},
                    "tag": "block",
                },
                # get_outbound(configs[1], tag="ru_proxy"),
            ],
            "remarks": "🇳🇱 NL",
        }
    ]
    # headers={
    #     "Content-Type": "application/json",
    #     "profile-title": "base64:8J+mtCBDeWJlckRPRw==",
    # },


# @router.post(path="/{tv_code}")
# async def post_tv_code(tv_code: str, user: Users = Depends(validate)):
#     await TvCode.create(
#         code=tv_code,
#         connect_url=script_settings.api_url + "/marzban/connect/" + user.connect_url,
#     )
#     return {"success": True}
