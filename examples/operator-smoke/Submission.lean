import Mathlib

namespace Submission

theorem operator_smoke_bool_0 : ∀ (b : Bool), (¬(b = true)) = (b = false) := by
  intro b
  cases b <;> simp

end Submission
