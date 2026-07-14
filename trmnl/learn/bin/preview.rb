#!/usr/bin/env ruby
# Renders src/*.liquid against a merge-variables JSON file and screenshots each
# size with headless Chromium — a stand-in for `trmnlp serve` on machines whose
# Ruby is too old for it (needs: gem install --user-install liquid -v 4.0.4).
#
# Layout is accurate; the framework's clamp/overflow JS may not settle, and
# image-dither is applied by TRMNL's server-side bitmap conversion, not here.
#
#   bin/vars.js --worst > /tmp/v.json && bin/preview.rb /tmp/v.json out/
require "liquid"
require "json"
require "base64"
require "fileutils"
require "tmpdir"

module TrmnlFilters
  def base64_encode(input)
    Base64.strict_encode64(input.to_s)
  end
end
Liquid::Template.register_filter(TrmnlFilters)

root = File.expand_path("..", __dir__)
vars_file = ARGV[0] or abort("usage: preview.rb <vars.json> [outdir] [size...]")
out = ARGV[1] || Dir.mktmpdir("learn-preview")
FileUtils.mkdir_p(out)

sizes = ARGV[2..]
sizes = %w[full half_vertical half_horizontal quadrant] if sizes.nil? || sizes.empty?

# Only view--full sits directly in a screen; the others are mashup cells, so
# they must render inside the mashup container that gives them their box.
mashup = { "full" => nil, "half_vertical" => "1Lx1R",
           "half_horizontal" => "1Tx1B", "quadrant" => "2x2" }
siblings = { "full" => 0, "half_vertical" => 1, "half_horizontal" => 1, "quadrant" => 3 }

vars = JSON.parse(File.read(vars_file))
shared = File.read("#{root}/src/shared.liquid")
chromium = "/Applications/Chromium.app/Contents/MacOS/Chromium"

sizes.each do |size|
  src = "#{root}/src/#{size}.liquid"
  next unless File.exist?(src)

  tpl = Liquid::Template.parse(shared + File.read(src), error_mode: :strict)
  body = tpl.render(vars, strict_variables: false)
  warn "#{size}: #{tpl.errors}" unless tpl.errors.empty?

  cell = %(<div class="view view--#{size}">\n#{body}\n</div>)
  blank = %(<div class="view view--#{size}"></div>) * siblings[size]
  inner = mashup[size] ? %(<div class="mashup mashup--#{mashup[size]}">#{cell}#{blank}</div>) : cell

  html = <<~HTML
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <link rel="stylesheet" href="https://usetrmnl.com/css/latest/plugins.css">
      <script src="https://usetrmnl.com/js/latest/plugins.js"></script>
    </head>
    <body class="environment trmnl">
      <div class="screen screen--og">
    #{inner}
      </div>
    </body>
    </html>
  HTML

  page = "#{out}/#{size}.html"
  File.write(page, html)
  system(chromium, "--headless", "--disable-gpu", "--window-size=800,480",
         "--force-device-scale-factor=1", "--hide-scrollbars",
         "--virtual-time-budget=4000", "--screenshot=#{out}/#{size}.png",
         "file://#{page}", err: File::NULL)
  puts "#{out}/#{size}.png"
end
