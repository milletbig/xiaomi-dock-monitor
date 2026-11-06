"""Tests for the auto-start command builder and the generated tray icon."""

from __future__ import annotations

import unittest

from dockmon.autostart import build_command


class BuildCommandTest(unittest.TestCase):
    def test_frozen_build_uses_the_executable(self):
        self.assertEqual(
            build_command(r"C:\apps\dockmon.exe", True),
            r'"C:\apps\dockmon.exe" --tray',
        )

    def test_source_build_uses_the_module_entry(self):
        self.assertEqual(
            build_command(r"C:\Python\pythonw.exe", False),
            r'"C:\Python\pythonw.exe" -m dockmon --tray',
        )

    def test_commands_are_quoted_for_paths_with_spaces(self):
        command = build_command(r"C:\Program Files\dockmon.exe", True)
        self.assertTrue(command.startswith('"C:\\Program Files\\dockmon.exe"'))


class TrayIconTest(unittest.TestCase):
    def test_icon_is_square_and_transparent(self):
        from dockmon.tray import build_icon_image

        image = build_icon_image(64)
        self.assertIsNotNone(image)
        self.assertEqual(image.size, (64, 64))
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getpixel((0, 0))[3], 0)


if __name__ == "__main__":
    unittest.main()
