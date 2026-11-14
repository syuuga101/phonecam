import codecs
import json
import subprocess

import pytest

import phonecam_setup as ps

NET_PRIVATE = json.dumps({"profiles": [{"name": "Home", "alias": "Wi-Fi", "cat": 1}], "adapters": []})
NET_PUBLIC = json.dumps({"profiles": [{"name": "Cafe", "alias": "Wi-Fi", "cat": 0}], "adapters": []})
ALT = r"C:\Program Files (x86)\AltServer\AltServer.exe"
MDNS = r"C:\Program Files\Bonjour\mDNSResponder.exe"


def ps_rule(name, prog, prof, enabled=1, direction=1, action=2, display=None, proto="Any", lport="Any"):
    return {"name": name, "display": display or name, "en": str(enabled), "dir": str(direction), "act": str(action),
            "prof": str(prof), "prog": prog, "proto": proto, "lport": lport}


def ps_json(*rules):
    return json.dumps({"rules": list(rules)})


def netsh_ja(name, prog, profiles, enabled="はい", direction="入力", action="許可", proto="任意", lport="任意"):
    return f"""規則名:                               {name}
----------------------------------------------------------------------
有効:                                 {enabled}
方向:                                 {direction}
プロファイル:                         {profiles}
グループ:                             
ローカル IP:                          任意
リモート IP:                          任意
プロトコル:                           {proto}
ローカル ポート:                      {lport}
リモート ポート:                      任意
エッジ トラバーサル:                  いいえ
プログラム:                           {prog}
操作:                                 {action}
"""


def netsh_en(name, prog, profiles, enabled="Yes", direction="In", action="Allow", proto="Any", lport="Any"):
    return f"""Rule Name:                            {name}
----------------------------------------------------------------------
Enabled:                              {enabled}
Direction:                            {direction}
Profiles:                             {profiles}
Grouping:                             
LocalIP:                              Any
RemoteIP:                             Any
Protocol:                             {proto}
LocalPort:                            {lport}
RemotePort:                           Any
Edge traversal:                       No
Program:                              {prog}
Action:                               {action}
"""


def make_run(net=NET_PRIVATE, fw_json=None, netsh=None, ps_fail=False):
    """PowerShell のファイアウォール取得だけ差し替える。fw_json=None なら PowerShell は失敗扱い (netsh に戻る)。"""
    def run(cmd, timeout=20):
        if cmd[0].lower().startswith("powershell"):
            script = cmd[-1]
            if "Get-NetFirewall" in script:
                if ps_fail or fw_json is None:
                    return 1, "Get-NetFirewallRule : not recognized"
                return 0, fw_json
            return 0, net
        if cmd[0].lower() == "netsh":
            return (None, "") if netsh is None else (0, netsh)
        return None, ""
    return run


def diag(run):
    res = ps.run_diagnostics(run=run, itunes_status=lambda: {"state": ps.STATE_WARN}, usb_snapshot=lambda: {})
    return {r["id"]: r for r in res}


# ---- PowerShell JSON ----

def test_powershell_ok_japanese_user_both_profiles():
    fw = ps_json(ps_rule("{A1}", ALT, 6, display="AltServer"), ps_rule("{B1}", MDNS, 6, display="Bonjour サービス"))
    d = diag(make_run(fw_json=fw))
    assert d["firewall"]["state"] == ps.DIAG_OK
    assert d["firewall_bonjour"]["state"] == ps.DIAG_OK


def test_powershell_matches_by_program_even_when_name_is_unrelated():
    fw = ps_json(ps_rule("{GUID}", ALT.lower(), 2, display="allowed app"))
    assert diag(make_run(fw_json=fw))["firewall"]["state"] == ps.DIAG_OK


def test_powershell_profile_enum_names_are_read():
    fw = ps_json(ps_rule("x", ALT, "Private, Public", display="AltServer"))
    assert diag(make_run(fw_json=fw))["firewall"]["state"] == ps.DIAG_OK


def test_powershell_block_rule_wins_ng_with_rule_name():
    fw = ps_json(ps_rule("a", ALT, 6, display="AltServer"), ps_rule("b", ALT, 2, action=4, display="Block AltServer"))
    r = diag(make_run(fw_json=fw))["firewall"]
    assert r["state"] == ps.DIAG_NG
    assert "Block AltServer" in r["detail"]


def test_powershell_block_on_other_profile_is_ignored():
    fw = ps_json(ps_rule("a", ALT, 2, display="AltServer"), ps_rule("b", ALT, 4, action=4, display="Block AltServer"))
    assert diag(make_run(net=NET_PRIVATE, fw_json=fw))["firewall"]["state"] == ps.DIAG_OK


def test_powershell_profile_missing_is_warn_and_names_missing_profile():
    fw = ps_json(ps_rule("a", ALT, 2, display="AltServer"))
    r = diag(make_run(net=NET_PUBLIC, fw_json=fw))["firewall"]
    assert r["state"] == ps.DIAG_WARN
    assert "パブリック" in r["detail"] and "AltServer" in r["detail"]
    assert "プライベート" in r["detail"]  # 見つかったルールのプロファイルも書く


def test_powershell_no_rules_is_ng():
    r = diag(make_run(fw_json=ps_json()))["firewall"]
    assert r["state"] == ps.DIAG_NG


def test_powershell_disabled_or_outbound_rule_does_not_count():
    fw = ps_json(ps_rule("a", ALT, 6, enabled=2, display="AltServer"), ps_rule("b", ALT, 6, direction=2, display="AltServer out"))
    r = diag(make_run(fw_json=fw))["firewall"]
    assert r["state"] == ps.DIAG_NG
    assert "AltServer" in r["detail"]


def test_powershell_single_rule_not_wrapped_in_array_is_read():
    one = json.dumps({"rules": ps_rule("a", ALT, 6)})
    assert ps.parse_firewall_json(one)[0]["profiles"] == 6


def test_bonjour_by_udp_5353_rule_without_program():
    fw = ps_json(ps_rule("m", "", 2, display="mDNS (UDP-In)", proto="UDP", lport="5353"))
    assert diag(make_run(fw_json=fw))["firewall_bonjour"]["state"] == ps.DIAG_OK


def test_bonjour_missing_is_warn_block_is_ng():
    assert diag(make_run(fw_json=ps_json()))["firewall_bonjour"]["state"] == ps.DIAG_WARN
    fw = ps_json(ps_rule("m", MDNS, 6, action=4, display="Bonjour サービス"))
    assert diag(make_run(fw_json=fw))["firewall_bonjour"]["state"] == ps.DIAG_NG


def test_unknown_network_only_needs_some_allow_rule():
    fw = ps_json(ps_rule("a", ALT, 4, display="AltServer"))
    assert diag(make_run(net="garbage", fw_json=fw))["firewall"]["state"] == ps.DIAG_OK


# ---- netsh (PowerShell 失敗時) ----

def test_netsh_japanese_output_ok():
    text = "\n".join([netsh_ja("AltServer", ALT, "プライベート,パブリック"), netsh_ja("Bonjour サービス", MDNS, "プライベート,パブリック")])
    d = diag(make_run(netsh=text))
    assert d["firewall"]["state"] == ps.DIAG_OK
    assert d["firewall_bonjour"]["state"] == ps.DIAG_OK


def test_netsh_japanese_rule_named_by_exe_with_no_blank_lines():
    text = netsh_ja("altserver.exe", ALT, "ドメイン,プライベート,パブリック") + netsh_ja("x", r"C:\other.exe", "任意")
    assert diag(make_run(netsh=text))["firewall"]["state"] == ps.DIAG_OK


def test_netsh_japanese_block_is_ng():
    text = netsh_ja("AltServer", ALT, "プライベート") + "\n" + netsh_ja("AltServer 拒否", ALT, "プライベート", action="ブロック")
    r = diag(make_run(netsh=text))["firewall"]
    assert r["state"] == ps.DIAG_NG and "AltServer 拒否" in r["detail"]


def test_netsh_japanese_partial_profile_is_warn():
    r = diag(make_run(net=NET_PUBLIC, netsh=netsh_ja("AltServer", ALT, "プライベート")))["firewall"]
    assert r["state"] == ps.DIAG_WARN


def test_netsh_japanese_disabled_and_outbound():
    text = netsh_ja("AltServer", ALT, "プライベート", enabled="いいえ") + "\n" + netsh_ja("AltServer 出", ALT, "プライベート", direction="出力")
    assert diag(make_run(netsh=text))["firewall"]["state"] == ps.DIAG_NG


@pytest.mark.parametrize("direction", ["入力", "受信", "内向き", "In", "Inbound"])
def test_netsh_direction_variants_are_inbound(direction):
    rules = ps.parse_firewall_netsh(netsh_ja("AltServer", ALT, "プライベート", direction=direction))
    assert rules[0]["direction"] == "in"


def test_netsh_english_output_ok_and_block():
    ok = netsh_en("AltServer", ALT, "Private,Public") + "\n" + netsh_en("Bonjour Service", MDNS, "Domain,Private,Public")
    d = diag(make_run(netsh=ok))
    assert d["firewall"]["state"] == ps.DIAG_OK and d["firewall_bonjour"]["state"] == ps.DIAG_OK
    blocked = netsh_en("AltServer", ALT, "Private") + "\n" + netsh_en("Block", ALT, "Private", action="Block")
    assert diag(make_run(netsh=blocked))["firewall"]["state"] == ps.DIAG_NG


def test_netsh_udp_5353_rule_is_bonjour():
    text = netsh_ja("mDNS (UDP 受信)", "任意", "プライベート", proto="UDP", lport="5353")
    assert diag(make_run(netsh=text))["firewall_bonjour"]["state"] == ps.DIAG_OK


def test_legacy_parse_firewall_reads_japanese():
    res = ps.parse_firewall(netsh_ja("AltServer", ALT, "プライベート"))
    assert res == {"found": 1, "allow_in": True, "block": False}


def test_both_commands_fail_is_warn():
    d = diag(make_run(fw_json=None, netsh=None))
    assert d["firewall"]["state"] == ps.DIAG_WARN and d["firewall_bonjour"]["state"] == ps.DIAG_WARN


# ---- 文字コード ----

class _Done:
    def __init__(self, raw):
        self.stdout, self.returncode = raw, 0


@pytest.mark.parametrize("enc", ["cp932", "utf-8"])
def test_run_text_decodes_cp932_and_utf8(monkeypatch, enc):
    try:
        oem = codecs.lookup("oem").name  # Windows only; the OEM code page is cp932 on a Japanese PC
    except LookupError:
        oem = None
    if enc == "cp932" and oem not in (None, "cp932"):
        pytest.skip("OEM code page of this machine is %s, not cp932" % oem)
    raw = netsh_ja("AltServer", ALT, "プライベート").encode(enc)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done(raw))
    rc, txt = ps.run_text(["netsh", "advfirewall"])
    assert rc == 0 and "規則名" in txt and ps.parse_firewall_netsh(txt)[0]["action"] == "allow"
