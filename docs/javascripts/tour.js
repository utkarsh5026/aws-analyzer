/* The video tours: a service used in a notebook, cell by cell.

   It isn't a video file. It's a small player that types each call into a notebook cell, runs it, and shows the
   report it draws: the guides' own screenshots (docs/images/<figure>-{light,dark}.webp), settled in and scrolled
   through, with captions saying what to notice. That keeps it sharp at any size, adds nothing to download beyond
   the screenshots, and keeps it in step with them, since shots.py remakes them from the tool's real output.

   Something moves from the first frame: the title card builds itself (the service, the headline word by word, the
   chapters ahead), pip streams its install, keys are typed with a typist's rhythm, a run shows the kernel busy and
   then its time, and one cell flows into the next without leaving the notebook.

   What each tour shows is data, window.AA_TOURS, written by .claude/skills/demo/tour_video.py into tours.js from
   the figures' own calls. Everything on screen is a pure function of the playback time (renderAt), so seeking is
   instant and tour_video.py steps through it frame by frame to export the MP4s in docs/videos/.

   Usage: <div class="aa-tour" data-tour="lambda_functions"><div class="tour-screen"></div>
   <div class="tour-controls"></div><div class="tour-chapters"></div></div>. On a guide it plays by itself the first
   time it's mostly on screen (not with "reduce motion"). docs/tour.html is the same player full window:
   tour.html?service=lambda_functions, with &record to hide the controls and &theme=light|dark. */

(function () {
  "use strict";

  var STAGE_W = 1280;
  var STAGE_H = 720;
  var CHROME_H = 40;
  var OUT_LEFT = 90;
  var OUT_RIGHT = 40;
  var CAPTION_ROOM = 96;
  var IMAGES = "images/";
  var RUNNING_FOR = 0.75;
  var POSTER_AT = 2.6;
  var HANDOVER = 0.35;
  var BAR_CELLS = 32;
  var params = new URLSearchParams(location.search);
  var FORCED_THEME = themeNamed(params.get("theme"));
  var RECORDING = params.has("record");
  var DARK_QUERY = window.matchMedia("(prefers-color-scheme: dark)");
  var REDUCE_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");

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
  function easeOut(p) { return 1 - Math.pow(1 - p, 3); }
  function fade(t, from, length) { return clamp((t - from) / length, 0, 1); }
  function escapeHtml(text) { return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function clock(t) { return Math.floor(t / 60) + ":" + String(Math.floor(t % 60)).padStart(2, "0"); }

  /* Fade an element in from `at`, rising `rise` pixels into place, as the title and end cards' parts do. */
  function arrive(element, local, at, rise) {
    var shown = easeOut(fade(local, at, 0.45));
    element.style.opacity = shown.toFixed(3);
    element.style.transform = "translateY(" + Math.round((1 - shown) * rise) + "px)";
  }

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

  /* When a cell's typing starts and ends, when it runs, and when its output is in (tour_video.py has the same).
     scene.runs is how long the cell runs: pip's install takes longer than a report. */
  function timing(scene) {
    var typeStart = 0.5;
    var typeDuration = clamp(scene.code.length / 32, 0.5, 2.4);
    var runAt = typeStart + typeDuration + 0.35;
    return { typeStart: typeStart, typeDuration: typeDuration, runAt: runAt, outAt: runAt + (scene.runs || RUNNING_FOR) };
  }

  /* Where each key falls in the typing, from 0 to 1: a typist's rhythm, quicker inside a word, slower after a comma
     or bracket, a breath at each new line. The same code always gets the same rhythm. */
  function keystrokes(code) {
    var marks = [];
    var total = 0;
    for (var i = 0; i < code.length; i++) {
      var key = code[i];
      var weight = 0.7 + ((i * 7919) % 11) / 18;
      if (key === "\n") weight = 4.5;
      else if (key === " ") weight = 1.3;
      else if (",(=".indexOf(key) >= 0) weight = 1.6;
      total += weight;
      marks.push(total);
    }
    return marks.map(function (mark) { return mark / total; });
  }

  function keysTyped(marks, progress) {
    var typed = 0;
    while (typed < marks.length && marks[typed] <= progress + 1e-9) typed++;
    return typed;
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
    this.show(this.t);
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
          if (self.onEnd) self.onEnd();
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

  /* What a stopped player shows: the frame at t, except at the very start, where it shows the finished title card
     (the first frame of a tour is its title still building, which would look empty). */
  Player.prototype.show = function (t) {
    if (this.playing || t > 0.05 || RECORDING) {
      this.renderAt(t);
      return;
    }
    this.renderAt(POSTER_AT);
    this.t = 0;
    this.renderControls(0);
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
    var before = this.scenes[index - 1];
    var after = this.scenes[index + 1];
    var flowsIn = scene.kind === "cell" && before && before.kind === "cell";
    var flowsOut = scene.kind === "cell" && after && after.kind === "cell";
    var comesIn = index === 0 || flowsIn ? 1 : fade(local, 0, 0.35);
    var goesOut = !after || flowsOut ? 1 : 1 - fade(local, scene.dur - 0.3, 0.3);
    this.built.root.style.opacity = Math.min(comesIn, goesOut).toFixed(3);
    if (scene.kind === "cell") {
      this.renderHandover(scene, local, flowsIn, flowsOut);
      this.renderCell(scene, local);
    } else if (scene.kind === "title") {
      this.renderTitle(scene, local);
    } else {
      this.renderEnd(scene, local);
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
      this.built = {
        root: root, eyebrow: root.querySelector(".tour-eyebrow"),
        words: Array.prototype.slice.call(root.querySelectorAll("h1 .w")),
        text: root.querySelector("p"), small: root.querySelector(".tour-small"),
        items: Array.prototype.slice.call(root.querySelectorAll(".tour-calls li, .tour-ahead li")),
      };
      return;
    }
    root.className = "tour-window";
    var cellNumber = this.scenes.slice(0, index).filter(function (s) { return s.kind === "cell"; }).length + 1;
    root.innerHTML =
      '<div class="tour-chrome"><span class="tour-logo"></span>' +
      '<span class="tour-tab">' + escapeHtml(this.tour.notebook) + "</span>" +
      this.stepsHtml(scene) +
      '<span class="tour-kernel"><i></i>Python 3</span></div>' +
      '<div class="tour-body"><div class="tour-nb"><div class="tour-cell"><div class="tour-prompt"></div>' +
      '<div class="tour-code"></div><span class="tour-keys"><kbd>⇧ Shift</kbd> + <kbd>Enter</kbd></span>' +
      '<span class="tour-busy"><i></i></span><span class="tour-took"></span></div></div>' +
      '<div class="tour-out"></div></div><div class="tour-caption"></div>';
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
        element.innerHTML = spec.lines.map(function (line) { return "<div>" + line.html + "</div>"; }).join("");
      }
      out.appendChild(element);
      var travel = Math.min(spec.panMax != null ? spec.panMax : Infinity, Math.max(0, height - room));
      return { element: element, spec: spec, travel: travel, lines: element.children };
    });
    this.built = {
      root: root, code: code, outputs: outputs, cellNumber: cellNumber, marks: keystrokes(scene.code),
      body: root.querySelector(".tour-body"), prompt: root.querySelector(".tour-prompt"),
      keys: root.querySelector(".tour-keys"), busy: root.querySelector(".tour-busy"),
      took: root.querySelector(".tour-took"), kernel: root.querySelector(".tour-kernel"),
      caption: root.querySelector(".tour-caption"),
    };
  };

  /* The toolbar's progress: a dot per call, the ones done filled, and this call's chapter. */
  Player.prototype.stepsHtml = function (scene) {
    if (!scene.step) return '<span class="tour-chip">' + escapeHtml(scene.label) + "</span>";
    var dots = "";
    for (var step = 1; step <= scene.steps; step++) {
      dots += "<i" + (step < scene.step ? ' class="done"' : step === scene.step ? ' class="on"' : "") + "></i>";
    }
    return '<span class="tour-chip"><span class="tour-dots">' + dots + "</span>" + escapeHtml(scene.chapter) + "</span>";
  };

  Player.prototype.titleHtml = function (scene) {
    var words = scene.heading.split(" ").map(function (word) { return '<span class="w">' + word + "</span>"; });
    var ahead = (scene.ahead || []).map(function (chapter, i) {
      return "<li><b>" + (i + 1) + "</b>" + escapeHtml(chapter) + "</li>";
    }).join("");
    return '<div class="tour-eyebrow"><img src="' + scene.icon + '" alt="" width="44" height="44">' +
      escapeHtml(scene.eyebrow) + "</div><h1>" + words.join(" ") + "</h1><p>" + scene.text + "</p>" +
      '<ol class="tour-ahead">' + ahead + "</ol>";
  };

  Player.prototype.endHtml = function (scene) {
    var calls = scene.calls.map(function (call) { return "<li>" + highlight(call) + "</li>"; }).join("");
    return '<div class="tour-eyebrow"><img src="' + scene.icon + '" alt="" width="44" height="44">' +
      escapeHtml(scene.eyebrow) + "</div><h1>" + scene.heading + '</h1><ul class="tour-calls">' + calls + "</ul>" +
      '<p class="tour-small">' + escapeHtml(scene.url) + "</p>";
  };

  /* The title card builds itself: the service, the headline word by word, a line under it, the chapters ahead,
     over a field of dots that drifts the whole time. */
  Player.prototype.renderTitle = function (scene, local) {
    var built = this.built;
    built.root.style.backgroundPosition = Math.round(local * 9) + "px " + Math.round(local * 5) + "px, 0 0";
    /* The service is already arriving on the first frame, so a video's first frame (its thumbnail) isn't empty. */
    arrive(built.eyebrow, local, -0.3, 18);
    built.words.forEach(function (word, i) { arrive(word, local, 0.05 + i * 0.07, 26); });
    var afterWords = 0.05 + built.words.length * 0.07;
    arrive(built.text, local, afterWords + 0.05, 16);
    built.items.forEach(function (item, i) { arrive(item, local, afterWords + 0.35 + i * 0.11, 12); });
    var leaving = fade(local, scene.dur - 0.45, 0.45);
    built.root.style.transform = "scale(" + (1 + 0.04 * ease(leaving)).toFixed(4) + ")";
  };

  /* The end card: the calls arrive one by one, then where the guide is. */
  Player.prototype.renderEnd = function (scene, local) {
    var built = this.built;
    built.root.style.backgroundPosition = Math.round(local * 9) + "px " + Math.round(local * 5) + "px, 0 0";
    arrive(built.eyebrow, local, 0.1, 14);
    built.words.forEach(function (word) { arrive(word, local, 0.25, 20); });
    built.items.forEach(function (item, i) { arrive(item, local, 0.55 + i * 0.16, 12); });
    arrive(built.small, local, 0.75 + built.items.length * 0.16, 10);
  };

  /* One cell flows into the next inside the same notebook: the old cell and its output slide up and fade, the new
     one rises in, and the toolbar stays put. */
  Player.prototype.renderHandover = function (scene, local, flowsIn, flowsOut) {
    var arriving = flowsIn ? easeOut(fade(local, 0, HANDOVER)) : 1;
    var leaving = flowsOut ? ease(fade(local, scene.dur - HANDOVER, HANDOVER)) : 0;
    var shift = (1 - arriving) * 40 - leaving * 40;
    this.built.body.style.opacity = Math.min(arriving, 1 - leaving).toFixed(3);
    this.built.body.style.transform = "translateY(" + Math.round(shift) + "px)";
  };

  Player.prototype.renderCell = function (scene, local) {
    var built = this.built;
    var times = timing(scene);
    var progress = clamp((local - times.typeStart) / times.typeDuration, 0, 1);
    var typed = keysTyped(built.marks, progress);
    var caretOn = local < times.runAt && (local < times.typeStart + times.typeDuration || Math.floor(local * 2.4) % 2 === 0);
    var html = highlight(scene.code.slice(0, typed)) + (caretOn ? '<span class="tour-caret"></span>' : "");
    if (built.code.innerHTML !== html) built.code.innerHTML = html;
    var running = local >= times.runAt && local < times.outAt;
    built.prompt.textContent = local < times.runAt ? "[ ]:" : running ? "[*]:" : "[" + built.cellNumber + "]:";
    built.root.classList.toggle("tour-running", running);
    built.kernel.classList.toggle("busy", running);
    this.renderRun(scene, local, times);
    this.renderOutputs(scene, local, times);
  };

  /* Running a cell: the keys that run it pop up, a sweep runs under the cell while the kernel is busy, then the
     cell says how long it took, as JupyterLab does. */
  Player.prototype.renderRun = function (scene, local, times) {
    var built = this.built;
    var keysIn = easeOut(fade(local, times.runAt - 0.35, 0.25));
    var keysOut = fade(local, times.runAt + 0.45, 0.3);
    built.keys.style.opacity = Math.min(keysIn, 1 - keysOut).toFixed(3);
    built.keys.style.transform = "scale(" + (0.85 + 0.15 * keysIn + 0.06 * Math.sin(Math.PI * fade(local, times.runAt - 0.05, 0.2))).toFixed(3) + ")";
    var running = local >= times.runAt && local < times.outAt;
    built.busy.style.opacity = running ? "1" : "0";
    var sweep = ((local - times.runAt) / 0.9) % 1;
    built.busy.firstChild.style.transform = "translateX(" + Math.round(-30 + sweep * 130) + "%)";
    var took = (times.outAt - times.runAt) * (scene.runs ? 1 : 0.6);
    built.took.textContent = "✓ " + took.toFixed(1) + "s";
    built.took.style.opacity = fade(local, times.outAt, 0.3).toFixed(3);
  };

  Player.prototype.renderOutputs = function (scene, local, times) {
    var built = this.built;
    built.outputs.forEach(function (output, i) {
      var spec = output.spec;
      if (spec.lines) {
        renderLines(output, local, times);
        return;
      }
      var shownAt = spec.at != null ? spec.at : times.outAt;
      var next = built.outputs[i + 1];
      var hideAt = next ? next.spec.at : scene.dur + 1;
      var opacity = Math.min(fade(local, shownAt, 0.4), 1 - fade(local, hideAt, 0.4));
      output.element.style.opacity = opacity.toFixed(3);
      output.element.style.visibility = opacity > 0 ? "visible" : "hidden";
      var panStart = shownAt + 1.6;
      var panEnd = Math.min(hideAt, scene.dur) - 1.4;
      var progress = panEnd > panStart ? ease(clamp((local - panStart) / (panEnd - panStart), 0, 1)) : 0;
      var settle = easeOut(fade(local, shownAt, 0.6));
      var rise = 18 * (1 - settle);
      /* Whole pixels: a report scrolled by a fraction of a pixel is re-drawn blurred, which costs an MP4 dearly. */
      output.element.style.transform = "translateY(" + Math.round(rise - progress * output.travel) + "px) scale(" +
        (1.015 - 0.015 * settle).toFixed(4) + ")";
    });
  };

  /* Streamed output, like pip's: each line shows at its time after the cell starts running, and a line with a bar
     fills it until the next line comes. */
  function renderLines(output, local, times) {
    var lines = output.spec.lines;
    output.element.style.opacity = local >= times.runAt ? "1" : "0";
    lines.forEach(function (line, i) {
      var at = times.runAt + line.at;
      var element = output.lines[i];
      element.style.opacity = fade(local, at, 0.12).toFixed(3);
      if (!line.bar) return;
      var until = i + 1 < lines.length ? times.runAt + lines[i + 1].at : at + 1;
      var filled = Math.round(BAR_CELLS * clamp((local - at) / Math.max(0.1, until - at - 0.1), 0, 1));
      var bar = element.querySelector(".bar");
      var drawn = "━".repeat(filled);
      if (bar && bar.dataset.filled !== String(filled)) {
        bar.dataset.filled = String(filled);
        bar.innerHTML = '<span class="full">' + drawn + "</span>" + "━".repeat(BAR_CELLS - filled);
      }
    });
  }

  Player.prototype.renderCaption = function (scene, local) {
    var element = this.built.caption;
    if (!element || !scene.captions || !scene.captions.length) return;
    var current = scene.captions[0];
    scene.captions.forEach(function (caption) { if (local >= caption[0]) current = caption; });
    if (element.dataset.at !== String(current[0])) {
      element.dataset.at = String(current[0]);
      element.innerHTML = current[1];
    }
    var shown = easeOut(fade(local - current[0], 0, 0.35));
    var leaving = scene.kind === "cell" ? fade(local, scene.dur - HANDOVER, HANDOVER) : 0;
    element.style.opacity = Math.min(shown, 1 - leaving).toFixed(3);
    element.style.transform = "translate(-50%, " + Math.round((1 - shown) * 14) + "px)";
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

  /* Play a guide's tour the first time it's mostly on screen, so it never sits there looking like a picture. */
  function playWhenSeen(box, player) {
    if (RECORDING || REDUCE_MOTION.matches || !("IntersectionObserver" in window)) return;
    var seen = new IntersectionObserver(function (entries) {
      if (!entries.some(function (entry) { return entry.isIntersecting; })) return;
      seen.disconnect();
      if (player.t < 0.05 && !player.playing) player.play();
    }, { threshold: 0.6 });
    seen.observe(box.querySelector(".tour-screen"));
  }

  /* ---- Clips: a screenshot's own short video. A ▶ Watch it run button under each screenshot that has a clip
     (window.AA_CLIPS, from clips.js) plays it in the picture's place: one cell typing the call (a report's, or the one
     that opens a window) and showing the screenshot under its caption, with the controls under it. When it ends, or
     on ✕ or Esc, the picture comes back. */
  var CLIP_FIGURE = /images\/([\w-]+)-light\.webp/;
  var CLIP_FADE_MS = 250;

  function figureName(figure) {
    var image = figure.querySelector('img[src*="-light.webp"]');
    var match = image && CLIP_FIGURE.exec(image.getAttribute("src"));
    return match ? match[1] : "";
  }

  function closeClip(figure, fade) {
    var box = figure.querySelector(".aa-clip");
    if (!box || box.dataset.closing) return;
    box.dataset.closing = "yes";
    box.player.pause();
    var picture = figure.querySelector(".aa-clip-picture");
    function putBack() {
      box.remove();
      picture.hidden = false;
      figure.classList.remove("aa-clip-open");
      var button = figure.querySelector(".aa-clip-play");
      if (button && !fade) button.focus({ preventScroll: true });
    }
    if (!fade) return putBack();
    box.classList.add("aa-clip-leaving");
    setTimeout(putBack, CLIP_FADE_MS);
  }

  function openClip(figure, clip) {
    document.querySelectorAll("figure.aa-clip-open").forEach(function (open) { closeClip(open, false); });
    var picture = figure.querySelector(".aa-clip-picture");
    var box = document.createElement("div");
    box.className = "aa-tour aa-clip";
    box.innerHTML = '<div class="tour-screen"></div><div class="tour-controls"></div>' +
      '<button type="button" class="aa-clip-close" aria-label="Back to the screenshot" title="Back to the screenshot">✕</button>';
    figure.insertBefore(box, picture);
    picture.hidden = true;
    figure.classList.add("aa-clip-open");
    box.player = new Player(box, clip);
    box.player.onEnd = function () { closeClip(figure, true); };
    box.querySelector(".aa-clip-close").addEventListener("click", function () { closeClip(figure, false); });
    box.player.play();
    box.querySelector(".aa-clip-close").focus({ preventScroll: true });
  }

  /* The button goes at the start of the caption, under the picture, so it covers nothing in the report. */
  function addClipButton(figure, clip) {
    var picture = figure.querySelector(":scope > p");
    if (!picture || figure.querySelector(".aa-clip-play")) return;
    picture.classList.add("aa-clip-picture");
    var seconds = Math.round(clip.scenes[0].dur);
    var button = document.createElement("button");
    button.type = "button";
    button.className = "aa-clip-play";
    button.innerHTML = '<span aria-hidden="true">▶</span> Watch it run <i>' + seconds + "s</i>";
    button.setAttribute("aria-label", "Watch it run: a " + seconds + "-second video of " +
      clip.scenes[0].code.split("\n").pop());
    button.addEventListener("click", function () { openClip(figure, clip); });
    var caption = figure.querySelector(":scope > figcaption");
    if (caption) caption.insertBefore(button, caption.firstChild);
    else figure.appendChild(button);
  }

  function initClips() {
    var clips = window.AA_CLIPS || {};
    document.querySelectorAll(".md-typeset figure").forEach(function (figure) {
      var clip = tourNamed(clips, figureName(figure));
      if (clip) addClipButton(figure, clip);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      document.querySelectorAll("figure.aa-clip-open").forEach(function (open) { closeClip(open, false); });
    });
  }

  function init() {
    initClips();
    var tours = window.AA_TOURS || {};
    document.querySelectorAll(".aa-tour").forEach(function (box) {
      var name = box.getAttribute("data-tour") || params.get("service");
      var tour = tourNamed(tours, name);
      if (!tour || box.dataset.tourReady) return;
      box.dataset.tourReady = "yes";
      var player = new Player(box, tour);
      playWhenSeen(box, player);
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
