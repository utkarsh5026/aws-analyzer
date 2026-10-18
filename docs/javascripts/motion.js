/* The guides' motion: what makes a page feel like it's being shown to you, not handed over.

   - Sections, figures, tiles and steps rise into place as they scroll into view, a group's items one after another.
   - The amber highlighter sweeps across a <mark> the first time it's seen, as if someone marked it while you watched.
   - A thin bar under the header shows how far through the guide you are.
   - A click on a screenshot opens it full size over the page; Esc, a click or the close button puts it back.

   Everything here is extra: without this script, or with "reduce motion" set in the system, every element is
   simply there (extra.css hides nothing unless this script has put .aa-motion on <html>). Elements already on
   screen when the page loads are left alone, so nothing that was painted blinks out and back in. */

(function () {
  "use strict";

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var canObserve = "IntersectionObserver" in window;

  /* What rises into view, and the groups whose items come in one after another (by their place in the group) */
  var REVEAL = [
    ".md-content__inner > h2", ".md-content__inner > p.gist", ".md-content__inner > figure",
    ".md-content__inner > .admonition", ".md-content__inner > p.part", ".md-content__inner > .md-typeset__scrollwrap",
    ".md-content__inner > .ref", ".home .section-lede", ".home .anatomy figure",
  ].join(",");
  var GROUPS = [
    ".quick > ul > li", ".grid.cards > ul > li", ".steps > ol > li", ".svc > li", ".never > div",
    ".anatomy ol > li", ".tr > .raw, .tr > .arrow, .tr > .said",
  ];
  var MAX_STAGGER = 8;

  function onScreen(element) {
    var box = element.getBoundingClientRect();
    return box.top < window.innerHeight && box.bottom > 0;
  }

  /* ---- Rising into view */
  function reveal(root) {
    var targets = Array.prototype.slice.call(root.querySelectorAll(REVEAL));
    GROUPS.forEach(function (selector) {
      var seen = new Map();
      root.querySelectorAll(selector).forEach(function (item) {
        var place = seen.get(item.parentNode) || 0;
        var stagger = item.parentNode.classList.contains("tr") ? place % 3 : place;
        item.style.setProperty("--aa-i", Math.min(stagger, MAX_STAGGER));
        seen.set(item.parentNode, place + 1);
        targets.push(item);
      });
    });

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("aa-in");
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });

    targets.forEach(function (element) {
      if (onScreen(element) || element.closest(".hero")) return;
      element.classList.add("aa-reveal");
      observer.observe(element);
    });
  }

  /* ---- The highlighter: a <mark> fills in from the left the first time it's on screen */
  function highlight(root) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("aa-in");
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -15% 0px", threshold: 1 });

    root.querySelectorAll(".md-typeset mark").forEach(function (mark) {
      if (mark.closest(".hero")) return;
      mark.classList.add("aa-sweep");
      observer.observe(mark);
    });
  }

  /* ---- How far through the page you are */
  function progress() {
    var header = document.querySelector(".md-header");
    if (!header || header.querySelector(".aa-progress")) return;
    var bar = document.createElement("div");
    bar.className = "aa-progress";
    bar.setAttribute("aria-hidden", "true");
    header.appendChild(bar);

    var pending = false;
    function draw() {
      pending = false;
      var room = document.documentElement.scrollHeight - window.innerHeight;
      var share = room > 0 ? Math.min(1, Math.max(0, window.scrollY / room)) : 0;
      bar.style.transform = "scaleX(" + share + ")";
      bar.classList.toggle("aa-shown", room > window.innerHeight * 0.5 && share > 0.002);
    }
    function schedule() {
      if (pending) return;
      pending = true;
      window.requestAnimationFrame(draw);
    }
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    draw();
  }

  /* ---- Screenshots full size: a click opens one over the page, with its caption */
  var zoom = null;
  var opener = null;

  function closeZoom() {
    if (!zoom || !zoom.classList.contains("aa-open")) return;
    zoom.classList.remove("aa-open");
    document.documentElement.classList.remove("aa-zoomed");
    if (opener) opener.focus({ preventScroll: true });
  }

  function makeZoom() {
    zoom = document.createElement("div");
    zoom.className = "aa-zoom";
    zoom.setAttribute("role", "dialog");
    zoom.setAttribute("aria-modal", "true");
    zoom.setAttribute("aria-label", "Screenshot, full size");
    zoom.innerHTML =
      '<button type="button" class="aa-zoom-x" aria-label="Close">✕</button>' +
      '<figure><img alt=""><figcaption></figcaption></figure>';
    zoom.addEventListener("click", closeZoom);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeZoom();
    });
    document.body.appendChild(zoom);
  }

  function openZoom(image) {
    if (!zoom) makeZoom();
    opener = image;
    var picture = zoom.querySelector("img");
    picture.src = image.currentSrc || image.src;
    picture.alt = image.alt;
    var caption = image.closest("figure").querySelector("figcaption");
    zoom.querySelector("figcaption").innerHTML = caption ? caption.innerHTML : "";
    document.documentElement.classList.add("aa-zoomed");
    zoom.classList.add("aa-open");
    zoom.querySelector(".aa-zoom-x").focus({ preventScroll: true });
  }

  function zoomable(root) {
    root.querySelectorAll(".md-typeset figure img").forEach(function (image) {
      if (image.closest(".aa-zoom")) return;
      image.classList.add("aa-zoomable");
      image.setAttribute("tabindex", "0");
      image.setAttribute("role", "button");
      image.setAttribute("aria-label", "Open full size: " + image.alt);
      image.addEventListener("click", function () { openZoom(image); });
      image.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        openZoom(image);
      });
    });
  }

  function start() {
    var root = document.querySelector(".md-content") || document;
    zoomable(root);
    progress();
    if (reduceMotion || !canObserve) return;
    document.documentElement.classList.add("aa-motion");
    reveal(root);
    highlight(root);
  }

  /* Material's instant loading, if it's ever turned on, swaps pages without reloading: document$ says when */
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(start);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
