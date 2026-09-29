-- At most one selected AI provider per user. Concurrent saves used to
-- deselect-all and insert in separate statements, so two selected=true
-- requests could both insert before either saw the other row.
WITH ranked_selected AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY user_id
            ORDER BY updated_at DESC, id DESC
        ) AS selected_rank
    FROM user_ai_provider_settings
    WHERE is_selected
)
UPDATE user_ai_provider_settings
SET is_selected = FALSE, updated_at = NOW()
WHERE id IN (
    SELECT id
    FROM ranked_selected
    WHERE selected_rank > 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_ai_provider_settings_one_selected
ON user_ai_provider_settings (user_id)
WHERE is_selected;
