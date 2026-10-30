import time
from mtcaptcha_client import MTCaptchaSolver

def main():
    print("=" * 60)
    print("          MTCaptcha Full Automated Solver")
    print("=" * 60)

    sitekey = "MTPublic-KzqLY1cKH"
    host_domain = "2captcha.com"

    solver = MTCaptchaSolver(sitekey=sitekey, host_domain=host_domain)

    # تشغيل دورة الحل حتى النجاح
    result = solver.solve_until_verified(max_retries=5)

    print("\n" + "=" * 60)
    print("             FINAL VERIFICATION SUMMARY")
    print("=" * 60)
    print(f"isVerified: {result['isVerified']}")
    if result["isVerified"]:
        print(f"[SUCCESS] Verified Token (vt):\n{result['verifiedToken']}")
    else:
        print(f"[!] Last OCR Text attempted: '{result.get('solutionText')}'")

if __name__ == "__main__":
    main()
