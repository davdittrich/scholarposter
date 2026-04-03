import re

test_cases = [
    ("10.1000/xyz123", "10.1000/xyz123"),
    ("10.1000/xyz123.", "10.1000/xyz123"),
    ("10.1000/xyz123!", "10.1000/xyz123"),
    ("10.1000/xyz123?", "10.1000/xyz123"),
    ("Did you read 10.1000/xyz123?", "10.1000/xyz123"),
    ("10.1000/xyz(2024)", "10.1000/xyz(2024)"),
    ("(10.1000/xyz(2024))", "10.1000/xyz(2024)"),
    ("10.1000/xyz(2024).", "10.1000/xyz(2024)"),
    ("10.1002/(SICI)1097-461X(1996)60:7<1234::AID-QUA1>3.0.CO;2-P", "10.1002/(SICI)1097-461X(1996)60:7<1234::AID-QUA1>3.0.CO;2-P"),
    ("10.1103/PhysRevLett.116.061102", "10.1103/PhysRevLett.116.061102"),
]

# Robust DOI regex
# 10\.\d{4,9}/      -> DOI Prefix
# (?:               -> Group for suffix
#   [-._;/:A-Z0-9<>] -> Plain characters (excluding parens)
#   |
#   \([-._;/:A-Z0-9<>]+\) -> Balanced parentheses
# )+
# (?<![.;,:\?\!])   -> Negative lookbehind for trailing punctuation
pattern = r"10\.\d{4,9}/(?:[-._;/:A-Z0-9<>]|\([-._;/:A-Z0-9<>]+\))+(?<![.;,:\?\!])"

def test():
    regex = re.compile(pattern, re.IGNORECASE)
    for text, expected in test_cases:
        match = regex.search(text)
        actual = match.group(0) if match else "No match"
        status = "PASS" if actual == expected else "FAIL"
        print(f"{status} | Input: {text:60} | Expected: {expected:40} | Actual: {actual}")

if __name__ == "__main__":
    test()
