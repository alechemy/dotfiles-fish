#!/usr/bin/env ruby
# Renders EVERY fact's full view and reports any that overflow the panel —
# vertically (content taller than the view) or horizontally (a code line wider
# than the column, i.e. clipped). Run after changing the corpus or the templates.
#
#   bin/overflow-check.rb
require "liquid"
require "json"
require "base64"
require "tmpdir"

module TrmnlFilters
  def base64_encode(input)
    Base64.strict_encode64(input.to_s)
  end
end
Liquid::Template.register_filter(TrmnlFilters)

root = File.expand_path("..", __dir__)
corpus = JSON.parse(File.read("#{root}/dist/corpus.json"))
shared = File.read("#{root}/src/shared.liquid")
tpl = Liquid::Template.parse(shared + File.read("#{root}/src/full.liquid"), error_mode: :strict)

cells = corpus["facts"].map do |f|
  lines = f["code"].to_s.empty? ? [] : f["code"].split("\n")
  vars = f.merge(
    "has_code" => !f["code"].to_s.empty?,
    "code_lines" => lines.size,
    "code_cols" => lines.map(&:length).max || 0,
  )
  body = tpl.render(vars, strict_variables: false)
  %(<div class="screen screen--og" data-id="#{f["id"]}"><div class="view view--full">#{body}</div></div>)
end

html = <<~HTML
  <!DOCTYPE html>
  <html><head><meta charset="utf-8">
  <link rel="stylesheet" href="https://usetrmnl.com/css/latest/plugins.css">
  <script src="https://usetrmnl.com/js/latest/plugins.js"></script>
  </head><body class="environment trmnl">
  #{cells.join("\n")}
  <script>
  window.addEventListener('load', function () {
    setTimeout(function () {
      var bad = [];
      document.querySelectorAll('.screen').forEach(function (s) {
        var view = s.querySelector('.view');
        var layout = s.querySelector('.layout');
        var pre = s.querySelector('pre.code');
        var vy = layout.scrollHeight - view.clientHeight;
        var px = pre ? pre.scrollWidth - pre.clientWidth : 0;
        if (vy > 0 || px > 0) {
          bad.push({ id: s.dataset.id, overflow_y: vy, code_clip_x: px });
        }
      });
      document.title = JSON.stringify({ checked: document.querySelectorAll('.screen').length, bad: bad });
      document.body.setAttribute('data-result', document.title);
    }, 1500);
  });
  </script></body></html>
HTML

Dir.mktmpdir("overflow") do |dir|
  page = "#{dir}/all.html"
  File.write(page, html)
  out = `/Applications/Chromium.app/Contents/MacOS/Chromium --headless --disable-gpu \
         --virtual-time-budget=20000 --window-size=800,480 --dump-dom "file://#{page}" 2>/dev/null`
  m = out.match(/data-result="(.*?)"/m)
  abort("could not read result from Chromium") unless m

  res = JSON.parse(m[1].gsub("&quot;", '"'))
  puts "checked #{res["checked"]} facts"
  if res["bad"].empty?
    puts "no overflow"
  else
    puts "#{res["bad"].size} OVERFLOWING:"
    res["bad"].each { |b| puts "  #{b["id"]}  vertical +#{b["overflow_y"]}px  code clipped +#{b["code_clip_x"]}px" }
    exit 1
  end
end
