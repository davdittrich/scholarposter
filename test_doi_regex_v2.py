import re

test_cases = [
    ("10.1000/xyz123", "10.1000/xyz123"),
    ("10.1000/xyz123.", "10.1000/xyz123"),
    ("10.1000/xyz123;", "10.1000/xyz123"),
    ("10.1000/xyz123,", "10.1000/xyz123"),
    ("10.1000/xyz(2024)", "10.1000/xyz(2024)"),
    ("(10.1000/xyz(2024))", "10.1000/xyz(2024)"), # This is the tricky one
    ("10.1000/xyz(2024).", "10.1000/xyz(2024)"),
    ("10.1038/nphys1170", "10.1038/nphys1170"),
    ("10.1002/(SICI)1097-461X(1996)60:7<1234::AID-QUA1>3.0.CO;2-P", "10.1002/(SICI)1097-461X(1996)60:7<1234::AID-QUA1>3.0.CO;2-P"),
    ("10.1002/(SICI)1097-461X(1996)60:7<1234::AID-QUA1>3.0.CO;2-P.", "10.1002/(SICI)1097-461X(1996)60:7<1234::AID-QUA1>3.0.CO;2-P"),
    ("10.1103/PhysRevLett.116.061102", "10.1103/PhysRevLett.116.061102"),
]

patterns = [
    (r"Option A (Basic + Lookbehind)", r"10\.\d{4,9}/[-._;()/:A-Z0-9<>]+(?<![.;,])"),
    (r"Option B (More inclusive lookbehind)", r"10\.\d{4,9}/[-._;()/:A-Z0-9<>]+(?<![.;,])"), # Same as A but I'll add more chars
    (r"Option C (Handle trailing parens)", r"10\.\d{4,9}/(?:[-._;()/:A-Z0-9<>]|\((?:[-._;()/:A-Z0-9<>]+)\))+(?<![.;,])"),
    (r"Option D (Expert recommendation variant)", r"10\.\d{4,9}/[-._;()/:A-Z0-9<>+]+(?<![.;,:\?\!])"),
]

def test_patterns():
    for name, p in patterns:
        print(f"--- Testing {name}: {p} ---")
        regex = re.compile(p, re.IGNORECASE)
        for text, expected in test_cases:
            match = regex.search(text)
            actual = match.group(0) if match else "No match"
            status = "PASS" if actual == expected else "FAIL"
            print(f"{status} | Input: {text:60} | Expected: {expected:40} | Actual: {actual}")
        print()

if __name__ == "__main__":
    test_patterns()
