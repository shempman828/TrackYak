import colorsys


def generate_community_palette(count):
    """Return `count` distinct hex colors for community coloring."""
    # Golden-angle hue steps keep every prefix spread out; mid-high S/V keeps dark labels legible.
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
        palette.append(f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}")
    return palette
