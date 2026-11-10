/* The video tours: a service used in a notebook, cell by cell.

   It isn't a video file. It's a small player that types each call into a notebook cell, runs it, and shows the
   report it draws: the guides' own screenshots (docs/images/<figure>-{light,dark}.webp), faded in and scrolled
   through, with captions saying what to notice. That keeps it sharp at any size, adds nothing to download beyond
   the screenshots, and keeps it in step with them, since shots.py remakes them from the tool's real output.

   What each tour shows is data, window.AA_TOURS, written by .claude/skills/demo/tour_video.py into tours.js from
   the figures' own calls. Everything on screen is a pure function of the playback time (renderAt), so seeking is
   instant and tour_video.py steps through it frame by frame to export the MP4s in docs/videos/.

   Usage: <div class="aa-tour" data-tour="lambda_functions"><div class="tour-screen"></div>
   <div class="tour-controls"></div><div class="tour-chapters"></div></div>. docs/tour.html is the same player
   full window: tour.html?service=lambda_functions, with &record to hide the controls and &theme=light|dark. */

(function () {
  "use strict";

  var STAGE_W = 1280;
  var STAGE_H = 720;
  var CHROME_H = 40;
  var OUT_LEFT = 90;
  var OUT_RIGHT = 40;
  var CAPTION_ROOM = 96;
  var IMAGES = "images/";
  var params = new URLSearchParams(location.search);
  var FORCED_THEME = themeNamed(params.get("theme"));
  var DARK_QUERY = window.matchMedia("(prefers-color-scheme: dark)");

  /* "light" or "dark" for those words and "" for anything else, so what reaches a URL is always one of two strings. */
  function themeNamed(word) {
    if (word === "dark") return "dark";
    if (word === "light") return "light";
    return "";
  }

  /* The tour with this name, looked up among the tours' own keys only (so "__proto__" finds nothing). */
  function tourNamed(tours, name) {
    return Object.prototype.hasOwnProperty.call(tours, name) ? tours[name] : null;
  }

  /* Light or dark: the theme picked with ?theme=, else Material's (its slate scheme is dark), else the system's. */
  function currentTheme() {
    if (FORCED_THEME) return FORCED_THEME;
    var scheme = document.body && document.body.getAttribute("data-md-color-scheme");
    if (scheme) return scheme === "slate" ? "dark" : "light";
    return DARK_QUERY.matches ? "dark" : "light";
  }

  function clamp(x, low, high) { return Math.min(high, Math.max(low, x)); }
  function ease(p) { return p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2; }
  function fade(t, from, length) { return clamp((t - from) / length, 0, 1); }
  function escapeHtml(text) { return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function clock(t) { return Math.floor(t / 60) + ":" + String(Math.floor(t % 60)).padStart(2, "0"); }

  /* A small Python highlighter: comments, strings, keywords, numbers and calls. Works on code typed half-way. */
  var TOKENS = /(#[^\n]*)|("[^"\n]*"?|'[^'\n]*'?)|\b(from|import|True|False|None|for|in)\b|(%pip)|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_]\w*)(?=\()/g;
  function highlight(code) {
    var out = "";
    var last = 0;
    var match;
    TOKENS.lastIndex = 0;
    while ((match = TOKENS.exec(code))) {
      out += escapeHtml(code.slice(last, match.index));
      var kind = match[1] ? "cm" : match[2] ? "st" : match[3] || match[4] ? "kw" : match[5] ? "nu" : "fn";
      out += '<span class="' + kind + '">' + escapeHtml(match[0]) + "</span>";
      last = match.index + match[0].length;
    }
    return out + escapeHtml(code.slice(last));
  }

  /* When a cell's typing starts and ends, when it runs, and when its output appears (tour_video.py has the same). */
  function timing(scene) {
    var typeStart = 0.5;
    var typeDuration = clamp(scene.code.length / 32, 0.5, 2.4);
    var runAt = typeStart + typeDuration + 0.35;
    return { typeStart: typeStart, typeDuration: typeDuration, runAt: runAt, outAt: runAt + 0.75 };
  }

  function Player(box, tour) {
    this.box = box;
    this.tour = tour;
    this.scenes = tour.scenes;
    var start = 0;
    this.scenes.forEach(function (scene) {
      scene.start = start;
      start += scene.dur;
    });
    this.duration = start;
    this.t = 0;
    this.playing = false;
    this.sceneIndex = -1;
    this.built = null;

    this.screen = box.querySelector(".tour-screen");
    this.stage = document.createElement("div");
    this.stage.className = "tour-stage";
    this.stage.setAttribute("aria-hidden", "true");
    this.screen.appendChild(this.stage);
    this.screen.setAttribute("role", "img");
    this.screen.setAttribute("aria-label", tour.summary);

    var self = this;
    this.fit();
    if ("ResizeObserver" in window) new ResizeObserver(function () { self.fit(); }).observe(this.screen);
    this.buildControls();
    this.screen.addEventListener("click", function () { self.toggle(); });
    this.setTheme(currentTheme());
    if (!FORCED_THEME) {
      DARK_QUERY.addEventListener("change", function () { self.followTheme(); });
      new MutationObserver(function () { self.followTheme(); })
        .observe(document.body, { attributes: true, attributeFilter: ["data-md-color-scheme"] });
    }
  }

  Player.prototype.followTheme = function () {
    if (currentTheme() !== this.theme) this.setTheme(currentTheme());
  };

  Player.prototype.picture = function (figure) {
    return IMAGES + figure + "-" + this.theme + ".webp";
  };

  /* Switch the notebook between light and dark, keeping the place in the tour, and point Download at that MP4. */
  Player.prototype.setTheme = function (theme) {
    this.theme = themeNamed(theme) || "light";
    var video = this.tour.video.replace(/-light\.mp4$/, "-" + this.theme + ".mp4");
    this.box.querySelectorAll("a[data-tour-mp4]").forEach(function (link) { link.href = video; });
    this.stage.classList.toggle("dark", this.theme === "dark");
    this.ready = this.preload();
    this.sceneIndex = -1;
    this.renderAt(this.t);
  };

  Player.prototype.fit = function () {
    var width = this.screen.clientWidth || STAGE_W;
    this.stage.style.transform = "scale(" + width / STAGE_W + ")";
  };

  Player.prototype.preload = function () {
    var self = this;
    var files = [];
    this.scenes.forEach(function (scene) {
      if (scene.icon) files.push(scene.icon);
      (scene.outputs || []).forEach(function (output) { if (output.img) files.push(self.picture(output.img)); });
    });
    return Promise.all(files.map(function (url) {
      var image = new Image();
      image.src = url;
      return image.decode().catch(function () {});
    }));
  };

  Player.prototype.buildControls = function () {
    var self = this;
    var controls = this.box.querySelector(".tour-controls");
    var chapters = this.box.querySelector(".tour-chapters");
    if (controls) {
      controls.innerHTML = '<button class="tour-play" type="button" aria-label="Play">▶</button>' +
        '<div class="tour-timeline" role="slider" tabindex="0" aria-label="Seek" aria-valuemin="0"></div>' +
        '<span class="tour-time"></span>';
      this.playButton = controls.querySelector(".tour-play");
      this.timeLabel = controls.querySelector(".tour-time");
      this.timeline = controls.querySelector(".tour-timeline");
      this.timeline.setAttribute("aria-valuemax", String(Math.round(this.duration)));
      this.segments = this.scenes.map(function (scene) {
        var segment = document.createElement("div");
        segment.className = "tour-seg";
        segment.style.flex = String(scene.dur);
        segment.title = scene.chapter;
        segment.appendChild(document.createElement("i"));
        self.timeline.appendChild(segment);
        return segment.firstChild;
      });
      this.playButton.addEventListener("click", function () { self.toggle(); });
      this.timeline.addEventListener("click", function (event) {
        var bounds = self.timeline.getBoundingClientRect();
        self.seek(((event.clientX - bounds.left) / bounds.width) * self.duration);
      });
      this.timeline.addEventListener("keydown", function (event) {
        if (event.key === "ArrowRight") self.seek(self.t + 5);
        if (event.key === "ArrowLeft") self.seek(self.t - 5);
      });
    }
    if (chapters) {
      this.chapterButtons = this.scenes.map(function (scene) {
        var button = document.createElement("button");
        button.type = "button";
        button.textContent = scene.chapter;
        button.addEventListener("click", function () {
          self.seek(scene.start);
          self.play();
        });
        chapters.appendChild(button);
        return button;
      });
    }
  };

  Player.prototype.toggle = function () {
    if (this.playing) this.pause();
    else this.play();
  };

  Player.prototype.play = function () {
    var self = this;
    if (this.t >= this.duration - 0.05) this.t = 0;
    this.playing = true;
    this.updateButton();
    this.ready.then(function () {
      if (!self.playing) return;
      var last = null;
      function tick(now) {
        if (!self.playing) return;
        if (last !== null) self.t = Math.min(self.duration, self.t + (now - last) / 1000);
        last = now;
        self.renderAt(self.t);
        if (self.t >= self.duration) {
          self.pause();
          return;
        }
        requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    });
  };

  Player.prototype.pause = function () {
    this.playing = false;
    this.updateButton();
  };

  Player.prototype.seek = function (t) {
    this.renderAt(clamp(t, 0, this.duration));
  };

  Player.prototype.updateButton = function () {
    var ended = !this.playing && this.t >= this.duration - 0.05;
    this.box.classList.toggle("tour-waiting", !this.playing && this.t < 0.05);
    if (!this.playButton) return;
    this.playButton.textContent = this.playing ? "❚❚" : ended ? "↻" : "▶";
    this.playButton.setAttribute("aria-label", this.playing ? "Pause" : "Play");
  };

  /* Draw the frame at time t (seconds). Pure: the same t always gives the same picture. */
  Player.prototype.renderAt = function (t) {
    this.t = t;
    var index = this.scenes.findIndex(function (scene) { return t < scene.start + scene.dur; });
    if (index < 0) index = this.scenes.length - 1;
    var scene = this.scenes[index];
    if (index !== this.sceneIndex) {
      this.sceneIndex = index;
      this.build(scene, index);
    }
    var local = t - scene.start;
    var isFirst = index === 0;
    var isLast = index === this.scenes.length - 1;
    var shown = Math.min(isFirst ? 1 : fade(local, 0, 0.35), isLast ? 1 : 1 - fade(local, scene.dur - 0.3, 0.3));
    this.built.root.style.opacity = shown.toFixed(3);
    if (scene.kind === "cell") {
      this.renderCell(scene, local);
    } else {
      var grow = isFirst ? 1 : 0.97 + 0.03 * ease(fade(local, 0, 0.8));
      this.built.root.style.transform = "scale(" + grow.toFixed(4) + ")";
    }
    this.renderCaption(scene, local);
    this.renderControls(index);
  };

  Player.prototype.build = function (scene, index) {
    this.stage.innerHTML = "";
    var root = document.createElement("div");
    this.stage.appendChild(root);
    if (scene.kind !== "cell") {
      root.className = "tour-card";
      root.innerHTML = scene.kind === "title" ? this.titleHtml(scene) : this.endHtml(scene);
      this.built = { root: root };
      return;
    }
    root.className = "tour-window";
    var cellNumber = this.scenes.slice(0, index).filter(function (s) { return s.kind === "cell"; }).length + 1;
    root.innerHTML =
      '<div class="tour-chrome"><span class="tour-logo"></span>' +
      '<span class="tour-tab">' + escapeHtml(this.tour.notebook) + "</span>" +
      '<span class="tour-chip">' + escapeHtml(scene.label) + "</span></div>" +
      '<div class="tour-nb"><div class="tour-cell"><div class="tour-prompt"></div><div class="tour-code"></div></div></div>' +
      '<div class="tour-out"></div><div class="tour-caption"></div>';
    var code = root.querySelector(".tour-code");
    var cell = root.querySelector(".tour-cell");
    var out = root.querySelector(".tour-out");
    code.innerHTML = highlight(scene.code);
    var outTop = CHROME_H + cell.offsetTop + cell.offsetHeight + 18;
    code.style.minHeight = code.offsetHeight + "px";
    out.style.top = outTop + "px";
    var outWidth = STAGE_W - OUT_LEFT - OUT_RIGHT;
    var room = STAGE_H - outTop - CAPTION_ROOM;
    var self = this;
    var outputs = scene.outputs.map(function (spec) {
      var element = document.createElement(spec.img ? "img" : "div");
      var height = 0;
      if (spec.img) {
        element.src = self.picture(spec.img);
        element.alt = "";
        height = spec.h * (outWidth / spec.w);
      } else {
        element.className = "tour-text";
        element.innerHTML = spec.text;
      }
      out.appendChild(element);
      var travel = Math.min(spec.panMax != null ? spec.panMax : Infinity, Math.max(0, height - room));
      return { element: element, spec: spec, travel: travel };
    });
    this.built = {
      root: root, code: code, outputs: outputs, cellNumber: cellNumber,
      prompt: root.querySelector(".tour-prompt"), caption: root.querySelector(".tour-caption"),
    };
  };

  Player.prototype.titleHtml = function (scene) {
    return '<div class="tour-eyebrow"><img src="' + scene.icon + '" alt="" width="44" height="44">' +
      escapeHtml(scene.eyebrow) + "</div><h1>" + scene.heading + "</h1><p>" + scene.text + "</p>";
  };

  Player.prototype.endHtml = function (scene) {
    var calls = scene.calls.map(function (call) { return "<li>" + highlight(call) + "</li>"; }).join("");
    return '<div class="tour-eyebrow"><img src="' + scene.icon + '" alt="" width="44" height="44">' +
      escapeHtml(scene.eyebrow) + "</div><h1>" + scene.heading + '</h1><ul class="tour-calls">' + calls + "</ul>" +
      '<p class="tour-small">' + escapeHtml(scene.url) + "</p>";
  };

  Player.prototype.renderCell = function (scene, local) {
    var built = this.built;
    var times = timing(scene);
    var typed = Math.floor(scene.code.length * clamp((local - times.typeStart) / times.typeDuration, 0, 1));
    var caretOn = local < times.runAt && Math.floor(local * 2.2) % 2 === 0;
    var html = highlight(scene.code.slice(0, typed)) + (caretOn ? '<span class="tour-caret"></span>' : "");
    if (built.code.innerHTML !== html) built.code.innerHTML = html;
    built.prompt.textContent = local < times.runAt ? "[ ]:" : local < times.outAt ? "[*]:" : "[" + built.cellNumber + "]:";
    built.root.classList.toggle("tour-running", local >= times.runAt && local < times.outAt);

    built.outputs.forEach(function (output, i) {
      var shownAt = output.spec.at != null ? output.spec.at : times.outAt;
      var next = built.outputs[i + 1];
      var hideAt = next ? next.spec.at : scene.dur + 1;
      var opacity = Math.min(fade(local, shownAt, 0.4), 1 - fade(local, hideAt, 0.4));
      output.element.style.opacity = opacity.toFixed(3);
      output.element.style.visibility = opacity > 0 ? "visible" : "hidden";
      var panStart = shownAt + 1.6;
      var panEnd = Math.min(hideAt, scene.dur) - 1.4;
      var progress = panEnd > panStart ? ease(clamp((local - panStart) / (panEnd - panStart), 0, 1)) : 0;
      var rise = 14 * (1 - ease(fade(local, shownAt, 0.5)));
      /* Whole pixels: a report scrolled by a fraction of a pixel is re-drawn blurred, which costs an MP4 dearly. */
      output.element.style.transform = "translateY(" + Math.round(rise - progress * output.travel) + "px)";
    });
  };

  Player.prototype.renderCaption = function (scene, local) {
    var element = this.built.caption;
    if (!element || !scene.captions || !scene.captions.length) return;
    var current = scene.captions[0];
    scene.captions.forEach(function (caption) { if (local >= caption[0]) current = caption; });
    if (element.dataset.at !== String(current[0])) {
      element.dataset.at = String(current[0]);
      element.innerHTML = current[1];
    }
    element.style.opacity = fade(local - current[0], 0, 0.3).toFixed(3);
  };

  Player.prototype.renderControls = function (index) {
    var self = this;
    if (this.segments) {
      this.scenes.forEach(function (scene, i) {
        var progress = clamp((self.t - scene.start) / scene.dur, 0, 1);
        self.segments[i].style.width = (progress * 100).toFixed(2) + "%";
      });
      this.timeLabel.textContent = clock(this.t) + " / " + clock(this.duration);
      this.timeline.setAttribute("aria-valuenow", String(Math.round(this.t)));
    }
    if (this.chapterButtons) {
      this.chapterButtons.forEach(function (button, i) { button.classList.toggle("on", i === index); });
    }
    this.updateButton();
  };

  function init() {
    var tours = window.AA_TOURS || {};
    document.querySelectorAll(".aa-tour").forEach(function (box) {
      var name = box.getAttribute("data-tour") || params.get("service");
      var tour = tourNamed(tours, name);
      if (!tour || box.dataset.tourReady) return;
      box.dataset.tourReady = "yes";
      var player = new Player(box, tour);
      if (!window.aaTour) {
        window.aaTour = {
          duration: player.duration,
          renderAt: function (t) { player.renderAt(t); },
          ready: function () { return player.ready; },
          play: function () { player.play(); },
          pause: function () { player.pause(); },
        };
      }
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
