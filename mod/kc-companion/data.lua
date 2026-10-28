data:extend({
  {
    type = "custom-input",
    name = "kc-toggle",
    key_sequence = "CONTROL + SHIFT + K",
    consuming = "none",
    order = "a",
  },
  {
    -- Kira's face (tools/draw_kira.py): top-bar button, window title, her lines in the feed
    type = "sprite",
    name = "kc-kira",
    filename = "__kc-companion__/graphics/kira.png",
    size = 64,
    flags = { "gui-icon" },
  },
})
