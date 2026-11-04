-- Request options used only for the ZH (translation) step. Translating doesn't need reasoning, and turning it off
-- keeps long translations from running into max_output_tokens (seen with GLM-4.7-Flash).
ALTER TABLE llm_models ADD COLUMN translate_options TEXT NOT NULL DEFAULT '{}';

UPDATE llm_models SET translate_options = '{"thinking":{"type":"disabled"}}' WHERE provider = 'zhipu';
UPDATE llm_models SET translate_options = '{"chat_template_kwargs":{"enable_thinking":false}}' WHERE provider = 'nvidia';
