(() => {
  const initMobileToc = () => {
    document.querySelectorAll(".rr-mobile-toc").forEach((element) => element.remove());

    const article = document.querySelector(".md-content__inner");
    const source = document.querySelector(
      ".md-sidebar--secondary [data-md-component='toc']",
    );
    const links = source ? [...source.querySelectorAll("a[href^='#']")] : [];
    if (!article || links.length < 2) return;

    const details = document.createElement("details");
    details.className = "rr-mobile-toc";

    const summary = document.createElement("summary");
    summary.textContent = "On this page";
    details.append(summary);

    const nav = document.createElement("nav");
    nav.setAttribute("aria-label", "On this page");
    const list = source.cloneNode(true);
    list.removeAttribute("data-md-component");
    nav.append(list);
    details.append(nav);

    details.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => {
        details.open = false;
      });
    });

    const heading = article.querySelector("h1");
    if (heading) heading.insertAdjacentElement("afterend", details);
  };

  const initDrawer = () => {
    const toggle = document.querySelector("#__drawer");
    const trigger = document.querySelector(".md-header label[for='__drawer']");
    const drawer = document.querySelector(".md-sidebar--primary");
    if (!toggle || !trigger || !drawer || trigger.dataset.rrDrawerEnhanced) return;

    trigger.dataset.rrDrawerEnhanced = "true";
    trigger.setAttribute("role", "button");
    trigger.setAttribute("tabindex", "0");
    trigger.setAttribute("aria-controls", "rr-site-navigation");
    drawer.id = "rr-site-navigation";
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-label", "Site navigation");

    const focusableElements = () =>
      [...drawer.querySelectorAll("a[href], button, [tabindex]")].filter(
        (element) =>
          !element.matches(".md-logo") &&
          !element.hasAttribute("disabled") &&
          element.getAttribute("tabindex") !== "-1" &&
          element.offsetParent !== null,
      );

    const syncState = (moveFocus = true) => {
      const open = toggle.checked;
      trigger.setAttribute("aria-expanded", String(open));
      drawer.setAttribute("aria-hidden", String(!open));
      if (open && moveFocus) {
        window.setTimeout(() => focusableElements()[0]?.focus(), 160);
      } else if (!open && moveFocus && drawer.contains(document.activeElement)) {
        trigger.focus();
      }
    };

    const closeDrawer = () => {
      if (!toggle.checked) return;
      toggle.checked = false;
      toggle.dispatchEvent(new Event("change", { bubbles: true }));
    };

    trigger.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        event.stopPropagation();
        toggle.checked = !toggle.checked;
        toggle.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });

    toggle.addEventListener("change", () => syncState());
    document.addEventListener("keydown", (event) => {
      if (!toggle.checked) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeDrawer();
        return;
      }
      if (event.key !== "Tab") return;

      const elements = focusableElements();
      const first = elements[0];
      const last = elements.at(-1);
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    syncState(false);
  };

  const init = () => {
    initMobileToc();
    initDrawer();
  };

  if (window.document$?.subscribe) {
    window.document$.subscribe(init);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
