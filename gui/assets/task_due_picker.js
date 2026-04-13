document.addEventListener("click", function (event) {
  const dateButton = event.target.closest("#btn-task-date-picker");
  const timeButton = event.target.closest("#btn-task-time-picker");
  const analysisDateButton = event.target.closest("#btn-analysis-when-date-picker");
  const analysisTimeButton = event.target.closest("#btn-analysis-when-time-picker");

  if (!dateButton && !timeButton && !analysisDateButton && !analysisTimeButton) return;

  const input = dateButton
    ? document.getElementById("task-due-date")
    : timeButton
      ? document.getElementById("task-due-time")
      : analysisDateButton
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
