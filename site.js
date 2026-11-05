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

  // Google tags load on every page with consent denied by default (consent
  // mode v2). Until a visitor accepts, Google receives only cookieless pings.
  // LinkedIn does not support consent mode, so it still waits for "accept".
  function loadGoogleTags() {
    window.dataLayer.push({
      "gtm.start": new Date().getTime(),
      event: "gtm.js"
    });
    addScript("https://www.googletagmanager.com/gtm.js?id=GTM-5XC8BSXV", "fc-gtm");

    addScript("https://www.googletagmanager.com/gtag/js?id=G-X8F5VE40V9", "fc-ga4");
    gtag("js", new Date());
    gtag("config", "G-X8F5VE40V9");
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

  function prepareToggles() {
    document.querySelectorAll("[data-toggle]").forEach(function (btn) {
      var targetId = btn.getAttribute("data-toggle");
      var target = document.getElementById(targetId);
      if (!target) return;
      btn.addEventListener("click", function () {
        var open = btn.getAttribute("aria-expanded") === "true";
        btn.setAttribute("aria-expanded", String(!open));
        target.hidden = open;
        if (!open) {
          var label = btn.querySelector("span");
          if (label && label.textContent.indexOf("Show") === 0) {
            label.dataset.originalText = label.dataset.originalText || label.textContent;
            label.textContent = label.textContent.replace("Show", "Hide");
          }
        } else {
          var label2 = btn.querySelector("span");
          if (label2 && label2.dataset.originalText) {
            label2.textContent = label2.dataset.originalText;
          }
        }
      });
    });
  }

  function prepareReferralCapture() {
    var params = new URLSearchParams(window.location.search);
    var source = params.get("utm_source") || params.get("ref") || "";
    var medium = params.get("utm_medium") || "";
    var campaign = params.get("utm_campaign") || "";
    var referrer = document.referrer || "";

    var parts = [];
    if (source) parts.push("source=" + source);
    if (medium) parts.push("medium=" + medium);
    if (campaign) parts.push("campaign=" + campaign);
    if (!source && referrer) parts.push("referrer=" + referrer);
    var summary = parts.length ? parts.join(", ") : "direct/unknown";

    document.querySelectorAll('input[name="Referral Source"]').forEach(function (field) {
      field.value = summary;
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

  function prepareResourceDownloads() {
    var endpoint = "https://formsubmit.co/ajax/hello@faisalconsulting.co.uk";

    document.querySelectorAll("form.resource-form").forEach(function (form) {
      form.addEventListener("submit", async function (event) {
        event.preventDefault();

        if (!form.checkValidity()) {
          form.reportValidity();
          return;
        }

        var nextField = form.querySelector('input[name="_next"]');
        var redirectUrl = nextField ? nextField.value : "";
        var submitButton = form.querySelector('button[type="submit"], input[type="submit"]');
        var originalLabel = submitButton
          ? (submitButton.tagName === "INPUT" ? submitButton.value : submitButton.textContent)
          : "";
        var errorMessage = form.querySelector(".form-error");

        if (!redirectUrl) {
          if (errorMessage) {
            errorMessage.hidden = false;
            errorMessage.textContent = "The download page is not configured. Please email hello@faisalconsulting.co.uk.";
          }
          return;
        }

        if (errorMessage) errorMessage.hidden = true;
        if (submitButton) {
          submitButton.disabled = true;
          if (submitButton.tagName === "INPUT") {
            submitButton.value = "Sending...";
          } else {
            submitButton.textContent = "Sending...";
          }
        }

        // If the email service is slow or down, still send the visitor to the
        // download page: they get the resource and the download still counts.
        // Only the lead email is lost, so flag it to GA4 (no personal data).
        var controller = "AbortController" in window ? new AbortController() : null;
        var timer = controller ? setTimeout(function () { controller.abort(); }, 8000) : null;
        try {
          var response = await fetch(endpoint, {
            method: "POST",
            body: new FormData(form),
            headers: { Accept: "application/json" },
            signal: controller ? controller.signal : undefined
          });
          if (!response.ok) throw new Error("Form submission failed: " + response.status);
        } catch (error) {
          window.dataLayer.push({
            event: "fc_form_backend_error",
            form_subject: (form.querySelector('input[name="_subject"]') || {}).value || "",
            error_type: error && error.name === "AbortError" ? "timeout" : "failed"
          });
          if (submitButton) {
            if (submitButton.tagName === "INPUT") submitButton.value = originalLabel;
            else submitButton.textContent = originalLabel;
          }
          await new Promise(function (resolve) { setTimeout(resolve, 400); });
        }
        if (timer) clearTimeout(timer);
        window.location.assign(redirectUrl);
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

  var calendlyAssetsLoading = false;
  var calendlyAssetsReady = false;
  var calendlyAssetWaiters = [];

  function whenCalendlyReady(callback) {
    if (calendlyAssetsReady && window.Calendly && window.Calendly.initPopupWidget) {
      callback();
      return;
    }
    calendlyAssetWaiters.push(callback);
    if (calendlyAssetsLoading) return;
    calendlyAssetsLoading = true;

    if (!document.getElementById("fc-calendly-css")) {
      var link = document.createElement("link");
      link.id = "fc-calendly-css";
      link.rel = "stylesheet";
      link.href = "https://assets.calendly.com/assets/external/widget.css";
      document.head.appendChild(link);
    }

    function finish() {
      calendlyAssetsReady = true;
      var waiters = calendlyAssetWaiters.slice();
      calendlyAssetWaiters = [];
      waiters.forEach(function (fn) { fn(); });
    }

    var existing = document.getElementById("fc-calendly-js");
    if (existing) {
      if (window.Calendly && window.Calendly.initPopupWidget) {
        finish();
      } else {
        existing.addEventListener("load", finish);
      }
      return;
    }

    var script = document.createElement("script");
    script.id = "fc-calendly-js";
    script.src = "https://assets.calendly.com/assets/external/widget.js";
    script.async = true;
    script.onload = finish;
    script.onerror = function () {
      calendlyAssetsLoading = false;
      calendlyAssetWaiters = [];
    };
    document.head.appendChild(script);
  }

  function calendlyEventName(data) {
    if (!data) return "";
    if (typeof data === "string") {
      try {
        data = JSON.parse(data);
      } catch (error) {
        return "";
      }
    }
    return data.event || "";
  }

  function prepareCalendlyEmbed() {
    // Confirmed bookings only: push dataLayer for GTM. Do not call gtag here
    // (Ads/GA4 would double-count once GTM forwards calendly_booked).
    window.addEventListener("message", function (event) {
      if (event.origin !== "https://calendly.com") return;
      if (calendlyEventName(event.data) !== "calendly.event_scheduled") return;
      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push({ event: "calendly_booked" });
    });

    document.addEventListener("click", function (event) {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      var link = event.target.closest
        ? event.target.closest('a[href*="calendly.com/hello-faisalconsulting"]')
        : null;
      if (!link) return;

      var url = link.href;
      if (!url) return;

      event.preventDefault();
      whenCalendlyReady(function () {
        if (window.Calendly && window.Calendly.initPopupWidget) {
          window.Calendly.initPopupWidget({ url: url });
          return;
        }
        window.location.assign(url);
      });
    });
  }

  if (savedConsent() === "accepted") loadTracking();
  loadGoogleTags();

  document.addEventListener("DOMContentLoaded", function () {
    prepareNavigation();
    prepareForms();
    prepareResourceDownloads();
    prepareReferralCapture();
    prepareToggles();
    prepareReveals();
    prepareConsentControls();
    prepareCalendlyEmbed();
  });
})();
