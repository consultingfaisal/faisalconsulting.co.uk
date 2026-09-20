(function () {
  "use strict";

  var endpoint = "https://formsubmit.co/ajax/hello@faisalconsulting.co.uk";

  function showError(form, message) {
    var error = form.querySelector(".resource-form-error");

    if (!error) {
      error = document.createElement("p");
      error.className = "resource-form-error";
      error.setAttribute("role", "alert");
      error.style.marginTop = "1rem";
      error.style.color = "#B5511D";
      form.appendChild(error);
    }

    error.textContent = message;
  }

  document.addEventListener("DOMContentLoaded", function () {
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

        if (!redirectUrl) {
          showError(form, "The download page is not configured. Please email hello@faisalconsulting.co.uk.");
          return;
        }

        if (submitButton) {
          submitButton.disabled = true;
          if (submitButton.tagName === "INPUT") {
            submitButton.value = "Sending…";
          } else {
            submitButton.textContent = "Sending…";
          }
        }

        var oldError = form.querySelector(".resource-form-error");
        if (oldError) oldError.remove();

        try {
          var response = await fetch(endpoint, {
            method: "POST",
            body: new FormData(form),
            headers: { Accept: "application/json" }
          });

          if (!response.ok) throw new Error("Form submission failed");

          window.location.assign(redirectUrl);
        } catch (error) {
          showError(form, "Sorry, the form could not be sent. Please try again or email hello@faisalconsulting.co.uk.");

          if (submitButton) {
            submitButton.disabled = false;
            if (submitButton.tagName === "INPUT") {
              submitButton.value = originalLabel;
            } else {
              submitButton.textContent = originalLabel;
            }
          }
        }
      });
    });
  });
})();
