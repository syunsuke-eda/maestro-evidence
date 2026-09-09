from __future__ import annotations

import json
import unittest

import support  # noqa: F401

from melib.hierarchy import compress, exclusions_for

CSV_SAMPLE = """element_num,depth,attributes,parent_num
0,0,"bounds=[0,0][0,0]",
1,1,"accessibilityText=Sample Dev; bounds=[0,0][402,874]; enabled=true; enabled=true",0
17,10,"bounds=[0,0][402,874]; enabled=true; enabled=true",12
18,11,"resource-id=album-detail.more; bounds=[354,66][402,114]; enabled=true; enabled=true",17
51,5,"value=SSID, Wi-Fi 3本中3本; text=SSID, Wi-Fi 3本中3本; resource-id=Wi-Fi; bounds=[315,26][332,38]; enabled=true",48
"""

JSON_SAMPLE = json.dumps(
    {
        "attributes": {"bounds": "[0,0][402,874]", "enabled": "true"},
        "children": [
            {
                "attributes": {
                    "text": "ログイン",
                    "resource-id": "login.submit",
                    "bounds": "[10,20][30,40]",
                    "enabled": "true",
                    "checked": "false",
                },
                "children": [],
            },
            {"attributes": {"bounds": "[0,0][1,1]"}, "children": []},
        ],
    }
)


ANDROID_CSV = """element_num,depth,attributes,parent_num
1,1,"resource-id=com.android.systemui:id/clock; text=12:34; bounds=[0,0][10,10]",0
2,1,"resource-id=com.android.systemui:id/battery; text=100%; bounds=[0,0][10,10]",0
3,1,"resource-id=com.example.app:id/submit; text=送信; bounds=[0,0][10,10]",0
"""

ANDROID_JSON = json.dumps(
    {
        "attributes": {"bounds": "[0,0][1,1]"},
        "children": [
            {"attributes": {"resource-id": "com.android.systemui:id/clock", "text": "12:34"}},
            {"attributes": {"resource-id": "com.example.app:id/submit", "text": "送信"}},
        ],
    }
)


class AndroidSystemUiTest(unittest.TestCase):
    """ステータスバー等はアプリの要素ではないので既定で落とす。"""

    def test_CSVでsystemuiの要素を除外する(self):
        lines = compress(ANDROID_CSV, exclude_resource_id_prefixes=exclusions_for("android"))
        self.assertEqual(len(lines), 1)
        self.assertIn("com.example.app:id/submit", lines[0])

    def test_JSONでもsystemuiの要素を除外する(self):
        lines = compress(ANDROID_JSON, exclude_resource_id_prefixes=exclusions_for("android"))
        self.assertEqual(len(lines), 1)
        self.assertIn("送信", lines[0])

    def test_除外指定が無ければ残る(self):
        self.assertEqual(len(compress(ANDROID_CSV)), 3)

    def test_iOSでは除外しない(self):
        self.assertEqual(exclusions_for("ios"), ())

    def test_アプリのresource_idは落とさない(self):
        lines = compress(ANDROID_CSV, exclude_resource_id_prefixes=exclusions_for("android"))
        self.assertTrue(all("systemui" not in line for line in lines))


class CompressTest(unittest.TestCase):
    def test_CSVから識別子を持つ要素だけ残す(self):
        lines = compress(CSV_SAMPLE)
        self.assertEqual(len(lines), 3)
        self.assertIn("accessibilityText=Sample Dev", lines[0])
        self.assertIn("resource-id=album-detail.more", lines[1])

    def test_CSVの重複属性は1つに潰す(self):
        line = compress(CSV_SAMPLE)[0]
        self.assertEqual(line.count("enabled=true"), 1)

    def test_CSVの値に含まれるカンマを壊さない(self):
        line = compress(CSV_SAMPLE)[2]
        self.assertIn("text=SSID, Wi-Fi 3本中3本", line)

    def test_JSONの入れ子を再帰して1行ずつにする(self):
        lines = compress(JSON_SAMPLE)
        self.assertEqual(len(lines), 1)
        self.assertIn("text=ログイン", lines[0])
        self.assertIn("resource-id=login.submit", lines[0])

    def test_仕様外の属性は出さない(self):
        self.assertNotIn("checked", compress(JSON_SAMPLE)[0])

    def test_識別子の無い要素は省く(self):
        self.assertEqual(compress(json.dumps({"attributes": {"bounds": "[0,0][1,1]"}})), [])

    def test_空入力は空になる(self):
        self.assertEqual(compress("  "), [])


if __name__ == "__main__":
    unittest.main()
