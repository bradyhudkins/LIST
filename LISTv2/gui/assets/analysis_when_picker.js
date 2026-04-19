document.addEventListener("click", function (event) {
  const dateButton = event.target.closest("#btn-analysis-when-date-picker");
  const timeButton = event.target.closest("#btn-analysis-when-time-picker");

  if (!dateButton && !timeButton) return;

  const input = dateButton
    ? document.getElementById("analysis-when-date")
    : document.getElementById("analysis-when-time");

  if (!input || input.disabled) return;

  try {
    if (typeof input.showPicker === "function") {
      input.showPicker();
      return;
    }
  } catch (err) {
    // Fall through to focus/click for browsers without showPicker support.
  }

  input.focus();
  input.click();
});
