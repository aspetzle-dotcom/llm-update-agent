(function () {
  "use strict";

  var root = document.documentElement;
  var toast = document.querySelector(".toast");
  var toastTimer;

  function say(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove("show"); }, 2200);
  }

  function effectiveTheme() {
    if (root.dataset.theme) return root.dataset.theme;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  var toggle = document.querySelector(".theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var next = effectiveTheme() === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("llm-updates-theme", next); } catch (e) {}
      document.dispatchEvent(new CustomEvent("themechange"));
    });
  }

  // Feedback buttons exist only in the local app; the static export shows a hint instead.
  document.querySelectorAll(".feedback").forEach(function (group) {
    group.addEventListener("click", function (event) {
      var button = event.target.closest("button[data-label]");
      if (!button) return;
      var label = button.dataset.label;
      fetch("api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entry_id: group.dataset.entry, label: label }),
      })
        .then(function (response) {
          return response.json().then(function (data) {
            if (!response.ok) throw new Error(data.error || "Could not save feedback");
            return data;
          });
        })
        .then(function () {
          group.querySelectorAll("button[data-label]").forEach(function (b) {
            b.setAttribute("aria-pressed", String(b === button));
          });
          say("Saved: " + button.textContent);
        })
        .catch(function (error) { say(error.message); });
    });
  });

  // Highlight the guide section currently on screen.
  var tocLinks = document.querySelectorAll(".toc a");
  if (tocLinks.length && "IntersectionObserver" in window) {
    var byId = {};
    tocLinks.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting || !byId[entry.target.id]) return;
        tocLinks.forEach(function (a) { a.classList.remove("active"); });
        byId[entry.target.id].classList.add("active");
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    document.querySelectorAll(".prose section[id]").forEach(function (s) { observer.observe(s); });
  }
})();
