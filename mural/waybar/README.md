# Mural Waybar Module

Shows the current wallpaper name with a colored dot matching its dominant color.

## Installation

### Automatic (from Mural Settings)

Open the Mural GUI → Settings → Linux Integration → Waybar → click **Install module**.

### Manual

```bash
mkdir -p ~/.local/share/mural/waybar
cp mural/waybar/mural-waybar.py ~/.local/share/mural/waybar/
cp mural/waybar/mural-waybar.css ~/.local/share/mural/waybar/
chmod +x ~/.local/share/mural/waybar/mural-waybar.py
```

## Waybar configuration

Add to `~/.config/waybar/config`:

```json
"custom/mural": {
    "exec": "~/.local/share/mural/waybar/mural-waybar.py",
    "interval": 5,
    "format": "{}",
    "tooltip": true,
    "return-type": "json"
}
```

Then add `"custom/mural"` to your modules list (e.g. `"modules-right"`).

## CSS

Import the stylesheet in your `~/.config/waybar/style.css`:

```css
@import "~/.local/share/mural/waybar/mural-waybar.css";
```

Or copy the rules manually:

```css
#custom-mural {
    color: inherit;
    padding: 0 8px;
}
#custom-mural.active {
    color: @foreground;
}
```

## How it works

1. When you export a palette in Mural (Library tab → **Export**), Mural writes
   `~/.cache/mural/current_palette.json` with the dominant colors.
2. The Waybar module reads this file every 5 seconds (configurable via `interval`).
3. The colored dot (⬤) is rendered in Pango markup using the dominant color.
4. The tooltip shows all palette swatches and the wallpaper path.

If the palette file does not exist the module shows nothing (empty text).
