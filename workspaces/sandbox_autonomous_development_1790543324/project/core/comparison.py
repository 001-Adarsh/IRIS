class ComparisonEngine:

    def compare(self, responses: dict) -> str:
        print("\n=== IRIS Comparison Engine ===")

        for provider, response in responses.items():
            print(f"\n[{provider.upper()}]")
            print(response)

        return "Comparison completed."
