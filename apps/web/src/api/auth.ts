import { apiFetch } from "../lib/apiClient";

export interface AccessTokenResponse {
  access_token: string;
  token_type: string;
}

export interface UserResponse {
  id: string;
  email: string;
  display_name: string;
}

export function register(
  email: string,
  password: string,
  displayName: string,
): Promise<UserResponse> {
  return apiFetch<UserResponse>("/v1/auth/register", {
    method: "POST",
    body: { email, password, display_name: displayName },
  });
}

export function login(email: string, password: string): Promise<AccessTokenResponse> {
  return apiFetch<AccessTokenResponse>("/v1/auth/login", {
    method: "POST",
    body: { email, password },
    skipAuthRetry: true,
  });
}

export function logout(): Promise<void> {
  return apiFetch<void>("/v1/auth/logout", { method: "POST" });
}

export function getMe(): Promise<UserResponse> {
  return apiFetch<UserResponse>("/v1/me");
}
