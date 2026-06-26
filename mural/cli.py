# mural/cli.py
# GPL v3 — see LICENSE

"""mural-cli — command-line interface for the Mural Core D-Bus service."""

from __future__ import annotations

import argparse
import json
import sys


def _get_proxy():
    """Return a dasbus proxy for com.mural.Core, or exit with an error."""
    try:
        from dasbus.connection import SessionMessageBus
        bus = SessionMessageBus()
        return bus.get_proxy("com.mural.Core", "/com/mural/Core")
    except Exception as exc:
        print(f"Cannot connect to Mural Core Service: {exc}", file=sys.stderr)
        print("Start it with: systemctl --user start mural-core.service", file=sys.stderr)
        sys.exit(1)


def cmd_set(args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    ok = bool(proxy.SetWallpaper(args.monitor, args.path, args.scaling))
    if ok:
        print(f"Applied '{args.path}' to {args.monitor}")
    else:
        print("Failed to apply wallpaper.", file=sys.stderr)
    return 0 if ok else 1


def cmd_get(args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    if args.monitor:
        wallpaper = proxy.GetCurrentWallpaper(args.monitor)
        print(wallpaper or "(none)")
    else:
        monitors = list(proxy.GetMonitors())
        for m in monitors:
            wp = proxy.GetCurrentWallpaper(m)
            print(f"{m}: {wp or '(none)'}")
    return 0


def cmd_pause(_args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    proxy.SetEnabled(False)
    print("Wallpaper rendering paused.")
    return 0


def cmd_resume(_args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    proxy.SetEnabled(True)
    print("Wallpaper rendering resumed.")
    return 0


def cmd_toggle(_args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    now_paused = bool(proxy.TogglePause())
    print("Wallpaper rendering paused." if now_paused else "Wallpaper rendering resumed.")
    return 0


def cmd_next(_args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    ok = bool(proxy.NextWallpaper())
    if not ok:
        print("Failed — no library wallpapers found.", file=sys.stderr)
    return 0 if ok else 1


def cmd_random(_args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    ok = bool(proxy.RandomWallpaper())
    if not ok:
        print("Failed — no library wallpapers found.", file=sys.stderr)
    return 0 if ok else 1


def cmd_status(_args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    status = proxy.GetStatus()
    running = bool(status.get("running", False))
    pid = int(status.get("pid", 0))
    version = str(status.get("version", "?"))
    desktop = str(status.get("desktop", "?"))
    monitors = list(status.get("monitors", []))
    print(f"Status:   {'running' if running else 'stopped'}")
    print(f"PID:      {pid or '(none)'}")
    print(f"Version:  {version}")
    print(f"Desktop:  {desktop}")
    print(f"Monitors: {', '.join(monitors) or '(none)'}")
    return 0


def cmd_monitors(_args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    monitors = list(proxy.GetMonitors())
    if not monitors:
        print("(no monitors detected)")
    for m in monitors:
        wp = proxy.GetCurrentWallpaper(m)
        print(f"{m}: {wp or '(none)'}")
    return 0


def cmd_palette(args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    monitor = args.monitor
    if not monitor:
        all_mons = list(proxy.GetMonitors())
        if not all_mons:
            print("No monitors detected.", file=sys.stderr)
            return 1
        monitor = all_mons[0]
    wp = proxy.GetCurrentWallpaper(monitor)
    if not wp:
        print("No wallpaper active on monitor.", file=sys.stderr)
        return 1
    try:
        from mural.utils.palette import extract_palette
        colors = extract_palette(wp)
    except Exception as exc:
        print(f"Error extracting palette: {exc}", file=sys.stderr)
        return 1
    if not colors:
        print("Could not extract palette from wallpaper.")
        return 1
    for color in colors:
        print(color)
    return 0


def cmd_now_playing(_args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    raw = proxy.GetNowPlaying()
    if not raw:
        print("Nothing playing.")
        return 0
    try:
        media = json.loads(raw)
        title = media.get("title") or "Unknown"
        artist = media.get("artist") or ""
        album = media.get("album") or ""
        print(f"Title:  {title}")
        if artist:
            print(f"Artist: {artist}")
        if album:
            print(f"Album:  {album}")
    except Exception:
        print(raw)
    return 0


def cmd_sddm_capture(_args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    print("Capturing SDDM screenshot…")
    ok = bool(proxy.CaptureSddmScreenshot())
    if ok:
        print("Done.")
    else:
        print("Capture failed.", file=sys.stderr)
    return 0 if ok else 1


def cmd_profile(args: argparse.Namespace) -> int:
    proxy = _get_proxy()
    sub = getattr(args, "profile_command", None)

    if sub == "list":
        try:
            profiles = json.loads(proxy.GetProfiles())
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if not profiles:
            print("No profiles saved.")
        for p in profiles:
            pid = p.get("id", "")
            print(f"{pid[:8]}…  {p['name']}  ({p.get('created_at', '')})")
        return 0

    if sub == "save":
        name = args.name
        try:
            profile_id = str(proxy.SaveProfile(name))
            print(f"Saved profile '{name}' (id: {profile_id[:8]}…)")
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    if sub == "load":
        profile_id = args.id
        try:
            ok = bool(proxy.LoadProfile(profile_id))
            if ok:
                print(f"Loaded profile {profile_id[:8]}…")
            else:
                print("Profile not found or failed to load.", file=sys.stderr)
                return 1
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    if sub == "delete":
        profile_id = args.id
        try:
            ok = bool(proxy.DeleteProfile(profile_id))
            if ok:
                print(f"Deleted profile {profile_id[:8]}…")
            else:
                print("Profile not found.", file=sys.stderr)
                return 1
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    print(f"Unknown profile subcommand: {sub}", file=sys.stderr)
    return 1


def cmd_hyprland_sync(args: argparse.Namespace) -> int:
    """One-shot Hyprland border color sync from the current primary wallpaper."""
    from mural.utils.hyprland import is_hyprland, set_border_color, set_inactive_border_color
    if not is_hyprland():
        print("Not running in a Hyprland session.", file=sys.stderr)
        return 1
    proxy = _get_proxy()
    monitors = list(proxy.GetMonitors())
    if not monitors:
        print("No monitors detected.", file=sys.stderr)
        return 1
    wp = proxy.GetCurrentWallpaper(monitors[0])
    if not wp:
        print("No wallpaper active.", file=sys.stderr)
        return 1
    try:
        from mural.utils.palette import extract_palette
        colors = extract_palette(wp)
    except Exception as exc:
        print(f"Palette extraction failed: {exc}", file=sys.stderr)
        return 1
    if not colors:
        print("Could not extract palette.", file=sys.stderr)
        return 1
    set_border_color(colors[0])
    print(f"Set Hyprland active border color to {colors[0]}")
    if args.inactive and len(colors) > 1:
        set_inactive_border_color(colors[1])
        print(f"Set Hyprland inactive border color to {colors[1]}")
    return 0


def cmd_openrgb_sync(args: argparse.Namespace) -> int:
    """One-shot OpenRGB lighting sync from the current primary wallpaper palette."""
    from mural.utils.openrgb import is_available, set_color_from_hex
    if not is_available():
        print("OpenRGB not reachable — enable the SDK server in OpenRGB settings.", file=sys.stderr)
        return 1
    proxy = _get_proxy()
    monitors = list(proxy.GetMonitors())
    if not monitors:
        print("No monitors detected.", file=sys.stderr)
        return 1
    wp = proxy.GetCurrentWallpaper(monitors[0])
    if not wp:
        print("No wallpaper active.", file=sys.stderr)
        return 1
    try:
        from mural.utils.palette import extract_palette
        colors = extract_palette(wp)
    except Exception as exc:
        print(f"Palette extraction failed: {exc}", file=sys.stderr)
        return 1
    if not colors:
        print("Could not extract palette.", file=sys.stderr)
        return 1
    source_map = {"dominant": 0, "secondary": 1, "tertiary": 2}
    idx = source_map.get(args.source, 0)
    color = colors[min(idx, len(colors) - 1)]
    set_color_from_hex(color)
    print(f"Set OpenRGB color to {color}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mural-cli",
        description="Control the Mural animated wallpaper platform from the terminal.",
    )
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # set
    p_set = sub.add_parser("set", help="Set wallpaper on a monitor")
    p_set.add_argument("monitor", help="Monitor name (e.g. DP-1)")
    p_set.add_argument("path", help="Path to wallpaper directory")
    p_set.add_argument(
        "--scaling", default="default",
        choices=["default", "stretch", "fit", "fill"],
        help="Scaling mode (default: default)",
    )

    # get
    p_get = sub.add_parser("get", help="Get current wallpaper on a monitor")
    p_get.add_argument("monitor", nargs="?", help="Monitor name; shows all if omitted")

    # pause / resume / toggle
    sub.add_parser("pause", help="Pause wallpaper rendering")
    sub.add_parser("resume", help="Resume wallpaper rendering")
    sub.add_parser("toggle", help="Toggle pause/resume")
    sub.add_parser("next", help="Switch to the next wallpaper in the library")
    sub.add_parser("random", help="Switch to a random wallpaper from the library")

    # status
    sub.add_parser("status", help="Show Core Service status")

    # monitors
    sub.add_parser("monitors", help="List monitors and their current wallpapers")

    # palette
    p_pal = sub.add_parser("palette", help="Print the color palette of the active wallpaper")
    p_pal.add_argument("--monitor", help="Monitor name; uses primary if omitted")

    # now-playing
    sub.add_parser("now-playing", help="Show MPRIS now-playing info")

    # sddm-capture
    sub.add_parser("sddm-capture", help="Capture current wallpaper as SDDM login background")

    # profile
    p_prof = sub.add_parser("profile", help="Manage monitor profiles")
    prof_sub = p_prof.add_subparsers(dest="profile_command", metavar="SUBCOMMAND")
    prof_sub.add_parser("list", help="List saved profiles")
    p_prof_save = prof_sub.add_parser("save", help="Save current assignments as a profile")
    p_prof_save.add_argument("name", help="Profile name")
    p_prof_load = prof_sub.add_parser("load", help="Load a saved profile")
    p_prof_load.add_argument("id", help="Profile ID (first 8 chars or full UUID)")
    p_prof_del = prof_sub.add_parser("delete", help="Delete a saved profile")
    p_prof_del.add_argument("id", help="Profile ID")

    # hyprland-sync
    p_hypr = sub.add_parser("hyprland-sync", help="One-shot Hyprland border color sync")
    p_hypr.add_argument(
        "--inactive", action="store_true",
        help="Also sync inactive border color to secondary palette color",
    )

    # openrgb-sync
    p_rgb = sub.add_parser("openrgb-sync", help="One-shot OpenRGB lighting sync")
    p_rgb.add_argument(
        "--source", default="dominant",
        choices=["dominant", "secondary", "tertiary"],
        help="Palette color to use (default: dominant)",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False):
        from mural import __version__
        print(f"mural-cli {__version__}")
        return

    cmd_map = {
        "set": cmd_set,
        "get": cmd_get,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "toggle": cmd_toggle,
        "next": cmd_next,
        "random": cmd_random,
        "status": cmd_status,
        "monitors": cmd_monitors,
        "palette": cmd_palette,
        "now-playing": cmd_now_playing,
        "sddm-capture": cmd_sddm_capture,
        "profile": cmd_profile,
        "hyprland-sync": cmd_hyprland_sync,
        "openrgb-sync": cmd_openrgb_sync,
    }

    if not args.command:
        parser.print_help()
        return

    handler = cmd_map.get(args.command)
    if handler is None:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)

    sys.exit(handler(args))


if __name__ == "__main__":
    main()
