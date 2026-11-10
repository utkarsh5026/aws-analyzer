/* Folding the sidebars: a small button on each one folds it to a thin rail, so the guide gets the width.

   - The navigation on the left folds to the services' icons: still one click to another guide.
   - The contents on the right folds to a dot per section, the one you're reading in orange.
   - A folded link keeps its name as a tooltip, and the choice is remembered in localStorage for the next page and
     the next visit (overrides/main.html puts it back before the page is drawn, so nothing flashes open).

   It only shows where the sidebars sit beside the page (extra.css's media queries); on a narrower screen they're
   Material's drawers, as before. Without this script both sidebars simply stay open. */

(function () {
  "use strict";

  var SIDES = [
    { key: "aa-nav", rail: "aa-nav-rail", sidebar: ".md-sidebar--primary", name: "navigation", foldTo: "‹", openTo: "›" },
    { key: "aa-toc", rail: "aa-toc-rail", sidebar: ".md-sidebar--secondary", name: "contents", foldTo: "›", openTo: "‹" },
  ];

  function remember(key, folded) {
    try {
      if (folded) localStorage.setItem(key, "rail");
      else localStorage.removeItem(key);
    } catch (error) { /* private browsing: the fold lasts for this page only */ }
  }

  function label(button, side, folded) {
    button.textContent = folded ? side.openTo : side.foldTo;
    button.title = (folded ? "Show the " : "Fold the ") + side.name;
    button.setAttribute("aria-label", button.title);
    button.setAttribute("aria-expanded", String(!folded));
  }

  function nameLinks(sidebar) {
    sidebar.querySelectorAll("a.md-nav__link").forEach(function (link) {
      if (!link.title) link.title = link.textContent.trim();
    });
  }

  function addButton(side) {
    var sidebar = document.querySelector(side.sidebar);
    var inner = sidebar && sidebar.querySelector(".md-sidebar__inner");
    if (!inner || !inner.querySelector(".md-nav__list")) return;

    var root = document.documentElement;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "aa-fold";
    label(button, side, root.classList.contains(side.rail));
    button.addEventListener("click", function () {
      var folded = root.classList.toggle(side.rail);
      remember(side.key, folded);
      label(button, side, folded);
    });

    nameLinks(sidebar);
    inner.insertBefore(button, inner.firstChild);
  }

  function start() {
    SIDES.forEach(addButton);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
