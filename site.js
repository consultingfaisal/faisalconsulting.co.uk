(function () {
  "use strict";

  var consentKey = "cookie_consent";
  var tagsLoaded = false;
  window.dataLayer = window.dataLayer || [];

  function gtag() {
    window.dataLayer.push(arguments);
  }

  gtag("consent", "default", {
    ad_storage: "denied",
    ad_user_data: "denied",
    ad_personalization: "denied",
    analytics_storage: "denied",
    wait_for_update: 500
  });

  function savedConsent() {
    try {
      return window.localStorage.getItem(consentKey);
    } catch (error) {
      return null;
    }
  }

  function saveConsent(value) {
    try {
      window.localStorage.setItem(consentKey, value);
    } catch (error) {
      return;
    }
  }

  function addScript(src, id) {
    if (document.getElementById(id)) return;
    var script = document.createElement("script");
    script.id = id;
    script.async = true;
    script.src = src;
    document.head.appendChild(script);
  }

  function loadTracking() {
    if (tagsLoaded) return;
    tagsLoaded = true;

    gtag("consent", "update", {
      ad_storage: "granted",
      ad_user_data: "granted",
      ad_personalization: "granted",
      analytics_storage: "granted"
    });

    window.dataLayer.push({
      "gtm.start": new Date().getTime(),
      event: "gtm.js"
    });
    addScript("https://www.googletagmanager.com/gtm.js?id=GTM-5XC8BSXV", "fc-gtm");

    addScript("https://www.googletagmanager.com/gtag/js?id=G-X8F5VE40V9", "fc-ga4");
    gtag("js", new Date());
    gtag("config", "G-X8F5VE40V9");

    window._linkedin_data_partner_ids = window._linkedin_data_partner_ids || [];
    if (window._linkedin_data_partner_ids.indexOf("10013452") === -1) {
      window._linkedin_data_partner_ids.push("10013452");
    }
    if (!window.lintrk) {
      window.lintrk = function (a, b) {
        window.lintrk.q.push([a, b]);
      };
      window.lintrk.q = [];
    }
    addScript("https://snap.licdn.com/li.lms-analytics/insight.min.js", "fc-linkedin");
  }

  function banner() {
    return document.getElementById("cookie-banner");
  }

  function showBanner() {
    var element = banner();
    if (!element) return;
    element.hidden = false;
    var accept = element.querySelector("[data-consent='accept']");
    if (accept) accept.focus();
  }

  function hideBanner() {
    var element = banner();
    if (element) element.hidden = true;
  }

  function chooseConsent(accepted) {
    saveConsent(accepted ? "accepted" : "declined");
    hideBanner();

    if (accepted) {
      loadTracking();
      return;
    }

    gtag("consent", "update", {
      ad_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied",
      analytics_storage: "denied"
    });

    if (tagsLoaded) window.location.reload();
  }

  function prepareNavigation() {
    var button = document.getElementById("nav-toggle");
    var menu = document.getElementById("nav-menu");
    if (!button || !menu) return;

    button.addEventListener("click", function () {
      var open = menu.classList.toggle("nav-open");
      button.setAttribute("aria-expanded", String(open));
      button.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    });

    menu.addEventListener("click", function (event) {
      if (!event.target.closest("a")) return;
      menu.classList.remove("nav-open");
      button.setAttribute("aria-expanded", "false");
      button.setAttribute("aria-label", "Open menu");
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !menu.classList.contains("nav-open")) return;
      menu.classList.remove("nav-open");
      button.setAttribute("aria-expanded", "false");
      button.setAttribute("aria-label", "Open menu");
      button.focus();
    });

    var current = window.location.pathname.split("/").pop() || "index.html";
    menu.querySelectorAll("a[href]").forEach(function (link) {
      var target = link.getAttribute("href").split("#")[0];
      if (target === current) link.setAttribute("aria-current", "page");
    });
  }

  function prepareForms() {
    document.querySelectorAll("input[type='text'], input[type='email']").forEach(function (input) {
      if (input.name === "_honey") {
        input.setAttribute("aria-hidden", "true");
        input.setAttribute("tabindex", "-1");
        input.setAttribute("autocomplete", "off");
        return;
      }
      if (!input.getAttribute("aria-label")) {
        input.setAttribute("aria-label", input.name || input.placeholder || "Form field");
      }
    });
  }

  function prepareResourceForms() {
    document.querySelectorAll("form.resource-form").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();

        var nextField = form.querySelector("input[name='_next']");
        var nextUrl = nextField ? nextField.value : null;
        var submitBtn = form.querySelector("button[type='submit']");
        var originalLabel = submitBtn ? submitBtn.textContent : "";
        var errorEl = form.querySelector(".form-error");

        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.textContent = "Sending...";
        }
        if (errorEl) errorEl.hidden = true;

        var actionUrl = form.getAttribute("action");
        var ajaxUrl = actionUrl.replace("formsubmit.co/", "formsubmit.co/ajax/");
        var formData = new FormData(form);

        fetch(ajaxUrl, {
          method: "POST",
          body: formData,
          headers: { "Accept": "application/json" }
        })
          .then(function (response) {
            if (!response.ok) throw new Error("Request failed");
            return response.json();
          })
          .then(function () {
            if (nextUrl) {
              window.location.href = nextUrl;
            } else if (submitBtn) {
              submitBtn.textContent = "Sent";
            }
          })
          .catch(function () {
            if (submitBtn) {
              submitBtn.disabled = false;
              submitBtn.textContent = originalLabel;
            }
            if (errorEl) {
              errorEl.hidden = false;
            } else {
              window.alert("Something went wrong sending your details. Please try again, or email hello@faisalconsulting.co.uk directly.");
            }
          });
      });
    });
  }

  function prepareReveals() {
    var items = document.querySelectorAll("[data-reveal]");
    if (!items.length) return;
    if (!("IntersectionObserver" in window)) {
      items.forEach(function (item) { item.classList.add("is-visible"); });
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.12 });
    items.forEach(function (item) { observer.observe(item); });
  }

  function prepareConsentControls() {
    var footerLine = document.querySelector(".site-footer p");
    if (footerLine && !footerLine.querySelector("[data-cookie-settings]")) {
      footerLine.appendChild(document.createTextNode(" · "));
      var settings = document.createElement("button");
      settings.type = "button";
      settings.className = "cookie-settings-link";
      settings.setAttribute("data-cookie-settings", "");
      settings.textContent = "Cookie settings";
      footerLine.appendChild(settings);
    }

    var element = banner();
    if (element) {
      var accept = element.querySelector("[data-consent='accept']");
      var decline = element.querySelector("[data-consent='decline']");
      if (accept) accept.addEventListener("click", function () { chooseConsent(true); });
      if (decline) decline.addEventListener("click", function () { chooseConsent(false); });
    }

    document.querySelectorAll("[data-cookie-settings]").forEach(function (button) {
      button.addEventListener("click", showBanner);
    });

    if (!savedConsent()) showBanner();
  }

  if (savedConsent() === "accepted") loadTracking();

  document.addEventListener("DOMContentLoaded", function () {
    prepareNavigation();
    prepareForms();
    prepareResourceForms();
    prepareReveals();
    prepareConsentControls();
  });
})();
