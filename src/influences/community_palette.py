import colorsys


def generate_community_palette(count):
    """Generate `count` maximally-distinct hex colors for Louvain community
    coloring.

    Hues step around the wheel by the golden-angle conjugate, which spreads
    every prefix of the sequence (not just the full set) roughly evenly
    around the circle — so even the first handful of communities look
    clearly different from one another, rather than only being guaranteed
    distinct once all `count` colors are in use. Saturation/value cycle
    across a few bands as a secondary cue for hues that land close together,
    and stay in a mid-high range so the fixed dark node-label text keeps
    enough contrast against every swatch.
    """
    golden_ratio_conjugate = 0.6180339887498949
    hue = 0.58  # anchors the first color near the app's existing indigo accent
    saturations = (0.68, 0.55, 0.78, 0.62)
    values = (0.90, 0.78, 0.95, 0.84)
    palette = []
    for i in range(count):
        hue = (hue + golden_ratio_conjugate) % 1.0
        s = saturations[i % len(saturations)]
        v = values[i % len(values)]
        r, g, b = colorsys.hsv_to_rgb(hue, s, v)
        palette.append(
            "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))
        )
    return palette
