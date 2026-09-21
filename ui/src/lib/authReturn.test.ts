import { describe, expect, it } from "vitest";
import { parseAuthReturn } from "./authReturn";

describe("parseAuthReturn", () => {
  it("leaves ordinary app routes alone", () => {
    expect(parseAuthReturn("")).toBeNull();
    expect(parseAuthReturn("#/")).toBeNull();
    expect(parseAuthReturn("#/runs/abc/review?state=x")).toBeNull();
  });

  it("recognises a password-reset link and keeps its token", () => {
    expect(parseAuthReturn("#access_token=tok123&expires_in=3600&refresh_token=r&token_type=bearer&type=recovery")).toEqual({ kind: "recovery", token: "tok123" });
  });

  it("treats any other sign-in link as a confirmed address", () => {
    expect(parseAuthReturn("#access_token=tok&type=signup")).toEqual({ kind: "confirmed" });
  });

  it("explains an expired link in plain words", () => {
    const r = parseAuthReturn("#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired");
    expect(r?.kind).toBe("error");
    expect(r && r.kind === "error" && r.message).toMatch(/expired/);
  });

  it("passes other Supabase errors through readably", () => {
    expect(parseAuthReturn("#error=server_error&error_description=Something+broke")).toEqual({ kind: "error", message: "Something broke" });
  });
});
