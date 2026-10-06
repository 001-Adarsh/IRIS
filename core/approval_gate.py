from pathlib import Path


class IRISApprovalGate:

    def request_approval(self, proposal):
        proposal_file = Path(
            proposal.get("proposal_file", "")
        )

        proposal_name = proposal_file.name

        print("\n=== IRIS UPGRADE PROPOSAL ===")
        print(f"Proposal: {proposal_name}")
        print(f"Objective: {proposal.get('objective')}")
        print(f"Workspace: {proposal.get('workspace')}")
        print(f"Changes: {', '.join(proposal.get('changes', []))}")
        print(
            f"Tests passed: "
            f"{proposal.get('test_results', {}).get('passed')}"
        )
        print("\nProduction changes are NOT yet authorized.")

        expected = f"APPROVE {proposal_name}".lower()

        answer = input(
            f"\nType exactly '{expected.upper()}' to authorize deployment: "
        ).strip().lower()

        return answer == expected
