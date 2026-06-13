/**
 * NYC Property Intel — lookup-page → chat handoff.
 *
 * Each lookup page has a form#lookup-form with a data-query-template
 * attribute containing an "{address}" placeholder. On submit we route to
 * /chat?q=<filled template>; chat.js prefills the input from ?q= and the
 * user confirms with Send (never auto-submitted).
 */
(function () {
  "use strict";

  var form = document.getElementById("lookup-form");
  if (!form) return;
  var input = document.getElementById("lookup-address");

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var address = input.value.trim();
    if (!address) {
      input.focus();
      return;
    }
    var template = form.getAttribute("data-query-template") || "{address}";
    var query = template.replace("{address}", address);
    if (typeof posthog !== "undefined") {
      posthog.capture("lookup_page_submit", { page: window.location.pathname });
    }
    window.location.href = "/chat?q=" + encodeURIComponent(query);
  });
})();
