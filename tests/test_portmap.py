import json
import tempfile
import unittest
from pathlib import Path

from dockmon.portmap import (
    DEFAULT_PORT_MAP,
    LEVEL_NESTED,
    ROLE_SS,
    ROLE_USB2,
    detect_hub_role,
    hub_level,
    is_top_level_hub,
    load_port_map,
)
from dockmon.usbhub import HubInfo


def make_hub(instance: str, description: str, pid: int) -> HubInfo:
    return HubInfo(instance, description, 0x2717, pid, "sym", True)


SS_HUB = make_hub(
    r"USB\VID_2717&PID_50BA\6&24176d9b&0&5", "Generic SuperSpeed USB Hub", 0x50BA
)
USB2_HUB = make_hub(
    r"USB\VID_2717&PID_50BB\6&24176d9b&0&1", "Generic USB Hub", 0x50BB
)
NESTED_HUB = make_hub(
    r"USB\VID_2717&PID_50BA\7&2ae5b31f&0&1", "Generic SuperSpeed USB Hub", 0x50BA
)
NESTED_USB2_HUB = make_hub(
    r"USB\VID_2717&PID_50BB\7&80fb6c5&0&1", "Generic USB Hub", 0x50BB
)


class CatalogueTests(unittest.TestCase):
    def test_physical_numbering(self):
        ports = DEFAULT_PORT_MAP.ports
        self.assertEqual([port.number for port in ports], [1, 2, 3, 4, 5])
        self.assertEqual([port.id for port in ports], ["pd", "usb_c", "usb_a1", "usb_a2", "usb_a3"])
        self.assertEqual(ports[0].display_label, "1 · 供电 Type-C")
        self.assertEqual(ports[1].display_label, "2 · Type-C 数据")
        self.assertEqual(ports[2].display_label, "3 · USB-A 1")
        self.assertFalse(ports[0].is_data)
        self.assertTrue(all(port.is_data for port in ports[1:]))

    def test_measured_hub_port_lookup(self):
        self.assertEqual(DEFAULT_PORT_MAP.lookup(USB2_HUB, 4).id, "usb_c")
        self.assertEqual(DEFAULT_PORT_MAP.lookup(SS_HUB, 2).id, "usb_a1")
        self.assertEqual(DEFAULT_PORT_MAP.lookup(NESTED_HUB, 3).id, "usb_a3")
        self.assertEqual(DEFAULT_PORT_MAP.lookup(NESTED_HUB, 4).id, "usb_a2")
        self.assertEqual(DEFAULT_PORT_MAP.lookup(NESTED_USB2_HUB, 3).id, "usb_a3")
        self.assertEqual(DEFAULT_PORT_MAP.lookup(NESTED_USB2_HUB, 4).id, "usb_a2")
        self.assertIsNone(DEFAULT_PORT_MAP.lookup(SS_HUB, 3))
        self.assertIsNone(DEFAULT_PORT_MAP.lookup(NESTED_USB2_HUB, 2))

    def test_hub_role_and_top_level_detection(self):
        self.assertEqual(detect_hub_role(SS_HUB), ROLE_SS)
        self.assertEqual(detect_hub_role(USB2_HUB), ROLE_USB2)
        self.assertEqual(hub_level(NESTED_HUB), LEVEL_NESTED)
        self.assertEqual(hub_level(NESTED_USB2_HUB), LEVEL_NESTED)
        self.assertTrue(is_top_level_hub(SS_HUB))
        self.assertTrue(is_top_level_hub(USB2_HUB))
        self.assertFalse(is_top_level_hub(NESTED_HUB))
        self.assertFalse(is_top_level_hub(NESTED_USB2_HUB))


class ConfigTests(unittest.TestCase):
    def _write(self, payload: dict) -> Path:
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        with handle:
            json.dump(payload, handle, ensure_ascii=False)
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return Path(handle.name)

    def _payload(self, refs: dict[str, list[dict]]) -> dict:
        rows = [
            ("pd", 1, "供电 Type-C", "usb_c"),
            ("usb_c", 2, "Type-C 数据", "usb_c"),
            ("usb_a1", 3, "USB-A 1", "usb_a"),
            ("usb_a2", 4, "USB-A 2", "usb_a"),
            ("usb_a3", 5, "USB-A 3", "usb_a"),
        ]
        return {
            "ports": [
                {
                    "number": number,
                    "id": physical_id,
                    "label": label,
                    "connector": connector,
                    "hub_ports": refs.get(physical_id, []),
                    "evidence": "test",
                }
                for physical_id, number, label, connector in rows
            ]
        }

    def test_repository_config_matches_measured_anchors(self):
        mapping = load_port_map()
        self.assertTrue(mapping.source.endswith("port_map.json"), mapping.source)
        self.assertEqual(mapping.lookup(SS_HUB, 2).id, "usb_a1")
        self.assertEqual(mapping.lookup(NESTED_HUB, 3).id, "usb_a3")
        self.assertEqual(mapping.lookup(NESTED_HUB, 4).id, "usb_a2")
        self.assertEqual(mapping.lookup(NESTED_USB2_HUB, 3).id, "usb_a3")
        self.assertEqual(mapping.lookup(NESTED_USB2_HUB, 4).id, "usb_a2")
        self.assertEqual(mapping.lookup(USB2_HUB, 4).id, "usb_c")
        self.assertEqual(mapping.warnings, ())

    def test_custom_config_is_loaded(self):
        payload = self._payload(
            {
                "usb_c": [{"role": "ss", "port": 4}],
                "usb_a1": [{"role": "ss", "port": 2}],
                "usb_a2": [{"role": "usb2", "port": 2}],
                "usb_a3": [{"role": "ss", "level": "nested", "port": 3}],
            }
        )
        mapping = load_port_map(self._write(payload))
        self.assertEqual(mapping.lookup(SS_HUB, 4).id, "usb_c")
        self.assertEqual(mapping.lookup(USB2_HUB, 2).id, "usb_a2")
        self.assertEqual(mapping.lookup(NESTED_HUB, 3).id, "usb_a3")
        self.assertEqual(mapping.warnings, ())

    def test_duplicate_mapping_is_reported(self):
        payload = self._payload(
            {
                "usb_c": [{"role": "ss", "port": 2}],
                "usb_a1": [{"role": "ss", "port": 2}],
            }
        )
        mapping = load_port_map(self._write(payload))
        self.assertEqual(len(mapping.warnings), 1)
        self.assertIn("SS Hub #2", mapping.warnings[0])

    def test_invalid_config_falls_back(self):
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        with handle:
            handle.write("{ invalid json")
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)

        mapping = load_port_map(handle.name)
        self.assertEqual(mapping.lookup(SS_HUB, 2).id, "usb_a1")
        self.assertEqual(mapping.lookup(USB2_HUB, 4).id, "usb_c")
        self.assertEqual(len(mapping.warnings), 1)

    def test_missing_config_uses_defaults(self):
        mapping = load_port_map("does-not-exist.json")
        self.assertEqual(mapping, DEFAULT_PORT_MAP)


if __name__ == "__main__":
    unittest.main()
