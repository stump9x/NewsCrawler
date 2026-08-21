import { describe, expect, it } from "vitest";
import { buildQuery, getApiBase, humanizeApiError } from "./client";

describe("buildQuery", () => {
  it("omits empty values", () => {
    expect(buildQuery({ a: "1", b: "", c: null, d: "x" })).toBe("?a=1&d=x");
  });
});

describe("getApiBase", () => {
  it("returns string without trailing slash when env empty-ish", () => {
    const base = getApiBase();
    expect(typeof base).toBe("string");
    expect(base.endsWith("/")).toBe(false);
  });
});

describe("humanizeApiError", () => {
  it("maps login 401 to Vietnamese credentials message", () => {
    expect(
      humanizeApiError(401, { detail: "Invalid credentials.", code: "invalid_credentials" }, { isLogin: true })
    ).toMatch(/mật khẩu/i);
  });

  it("maps invalid token to session expired message", () => {
    expect(humanizeApiError(401, { detail: "Invalid token." })).toMatch(/hết hạn|đăng nhập lại/i);
  });
});
