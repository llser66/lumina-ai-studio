export type ModelChoice = { id: string; name: string; input: string[]; type: string; reasoning?: boolean };
export type ProviderInfo = {
  id: string; name: string; api_key: boolean; oauth: boolean; subscription: boolean; login_label?: string;
  chat_models?: ModelChoice[]; image_models?: ModelChoice[];
};
export type ModelConnection = { id: string; provider: string; name: string; auth_method: string; hint: string; status: string; checked_at: number | null; subscription: boolean; image_models: ModelChoice[]; chat_models: ModelChoice[] };
export type ModelPreferences = { image_connection_id: string | null; image_model: string; text_connection_id: string | null; text_model: string };
export type ModelConfiguration = {
  owner_id: string;
  sdk: string; sdk_version: string; providers: ProviderInfo[]; connections: ModelConnection[];
  preferences: ModelPreferences; image_ready: boolean; active_image_model: string; server_default_available: boolean;
  image_controls: { quality: boolean; exact_size: boolean; edit: boolean };
};
export type OAuthFlow = {
  id: string; provider: string; status: string; message: string; auth_url?: string;
  verification_url?: string; user_code?: string; expires_at: number; connection_id?: string;
  stage?: string; error_code?: string; http_status?: number; retryable?: boolean;
  manual_callback?: boolean; manual_input_kind?: 'url' | 'code' | 'code_or_url';
  prompt?: { id: string; type: string; message: string; placeholder?: string; options?: { id: string; label: string; description?: string }[] };
};
