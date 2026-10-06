from pathlib import Path
import json

from core.sandbox import IRISSandbox
from core.test_runner import IRISTestRunner
from core.upgrade_manager import IRISUpgradeManager
from core.approval_gate import IRISApprovalGate
from core.deployment_manager import IRISDeploymentManager
from core.proposal_integrity import IRISProposalIntegrity
from core.sandbox_diff import IRISSandboxDiff


class IRISDevelopmentEngine:

    def __init__(self, project_root=None):
        self.project_root = Path(
            project_root or Path(__file__).resolve().parent.parent
        )

        self.sandbox = IRISSandbox()
        self.upgrades = IRISUpgradeManager(self.project_root)
        self.approval = IRISApprovalGate()
        self.deployment = IRISDeploymentManager(
            self.project_root
        )

    def create_development_workspace(self, name="development"):
        snapshot = self.sandbox.create_project_snapshot(name)

        print("\n=== IRIS DEVELOPMENT SANDBOX ===")
        print(f"Workspace: {snapshot['workspace']}")
        print(f"Project:   {snapshot['project']}")
        print("Production modified: NO")

        return snapshot

    def test_workspace(self, project_path):
        runner = IRISTestRunner(project_path)
        result = runner.run()

        print("\n=== IRIS SANDBOX TEST ===")
        print(f"Passed: {result['passed']}")

        return result

    def propose_upgrade(
        self,
        objective,
        workspace,
        changes,
        test_results
    ):
        return self.upgrades.create_proposal(
            objective=objective,
            workspace=workspace,
            changes=changes,
            test_results=test_results
        )

    def load_proposal(self, proposal_file):
        path = Path(proposal_file).resolve()

        try:
            path.relative_to(
                (self.project_root / "upgrade_proposals").resolve()
            )
        except ValueError:
            raise PermissionError(
                "Proposal must be inside upgrade_proposals."
            )

        if not path.is_file():
            raise FileNotFoundError(
                f"Proposal not found: {proposal_file}"
            )

        return json.loads(
            path.read_text(encoding="utf-8")
        )

    def approve_and_deploy(self, proposal_file):

        proposal_path = Path(
            proposal_file
        ).resolve()

        proposal = self.load_proposal(
            proposal_path
        )

        proposal["proposal_file"] = str(
            proposal_path
        )

        # ----------------------------------------------------
        # PROPOSAL DIGEST VERIFICATION
        # ----------------------------------------------------

        print("\n=== IRIS PROPOSAL DIGEST CHECK ===")

        digest_check = (
            self.upgrades.verify_proposal_digest(
                proposal
            )
        )

        if not digest_check.get("passed"):

            print("Proposal digest: FAILED")
            print("Deployment cancelled.")

            return {
                "status": "integrity_failed",
                "production_modified": False,
                "reason": digest_check.get(
                    "reason",
                    "Proposal integrity verification failed."
                ),
                "integrity": digest_check
            }

        print("Proposal digest: PASSED")

        # ----------------------------------------------------
        # READ-ONLY SANDBOX / PRODUCTION DIFF
        # ----------------------------------------------------

        print("\n=== IRIS SANDBOX / PRODUCTION DIFF ===")

        sandbox_project = Path(proposal.get('workspace', '')) / 'project'
        diff_engine = IRISSandboxDiff(
            self.project_root,
            sandbox_project
        )

        diff_result = diff_engine.compare()

        print(
            f"Added files: {len(diff_result['added'])}"
        )

        print(
            f"Modified files: {len(diff_result['modified'])}"
        )

        print(
            f"Removed files: {len(diff_result['removed'])}"
        )

        if diff_result["added"]:
            print("\nAdded:")
            for path in diff_result["added"]:
                print(f"  + {path}")

        if diff_result["modified"]:
            print("\nModified:")
            for path in diff_result["modified"]:
                print(f"  ~ {path}")

        if diff_result["removed"]:
            print("\nRemoved:")
            for path in diff_result["removed"]:
                print(f"  - {path}")

        # The diff is informational and read-only.
        # It never changes production or sandbox files.



        # ----------------------------------------------------
        # CREATE APPROVAL-TIME INTEGRITY MANIFEST
        # ----------------------------------------------------

        workspace = Path(
            proposal.get(
                "workspace",
                ""
            )
        ).resolve()

        sandbox_project = (
            workspace / "project"
        )

        self.integrity = IRISProposalIntegrity(
            sandbox_project
        )

        try:
            approval_manifest = (
                self.integrity.create_manifest(
                    proposal.get(
                        "changes",
                        []
                    )
                )
            )
        except Exception as e:
            return {
                "status": "integrity_failed",
                "production_modified": False,
                "reason": (
                    f"Could not create approval integrity manifest: {e}"
                )
            }

        print("\n=== IRIS APPROVAL INTEGRITY ===")
        print(
            f"Protected files: {len(approval_manifest)}"
        )
        print(
            "SHA-256 manifest created before approval."
        )

        approved = self.approval.request_approval(
            proposal
        )

        if not approved:

            return {
                "status": "cancelled",
                "production_modified": False
            }

        # ----------------------------------------------------
        # VERIFY SANDBOX INTEGRITY AFTER APPROVAL
        # ----------------------------------------------------

        print("\n=== IRIS APPROVAL INTEGRITY CHECK ===")

        integrity_check = (
            self.integrity.verify_manifest(
                approval_manifest
            )
        )

        if not integrity_check.get("passed"):

            print(
                "Integrity check: FAILED"
            )

            print(
                "Deployment cancelled."
            )

            return {
                "status": "integrity_failed",
                "production_modified": False,
                "integrity": integrity_check
            }

        print(
            "Integrity check: PASSED"
        )

        print("\n=== IRIS DEPLOYMENT ===")
        print("Approval accepted.")
        print("Creating production backup...")

        deployment_result = self.deployment.deploy(
            proposal
        )

        # ----------------------------------------------------
        # DEPLOYMENT COPY FAILED
        # ----------------------------------------------------

        if deployment_result.get("status") != "deployed":

            proposal["status"] = deployment_result.get(
                "status",
                "failed"
            )

            proposal["production_modified"] = (
                deployment_result.get(
                    "production_modified",
                    False
                )
            )

            proposal["deployment"] = deployment_result

            proposal_path.write_text(
                json.dumps(
                    proposal,
                    indent=2
                ),
                encoding="utf-8"
            )

            return deployment_result

        print("\nDeployment copy completed.")
        print("Running post-deployment verification...")

        # ----------------------------------------------------
        # POST-DEPLOYMENT VERIFICATION
        # ----------------------------------------------------

        verification = (
            self.deployment.verify_deployment(
                proposal,
                deployment_result
            )
        )

        deployment_result["verification"] = verification

        if verification.get("passed"):

            print("Post-deployment verification: PASSED")

            proposal["status"] = "deployed"
            proposal["production_modified"] = True
            proposal["deployment"] = deployment_result

            proposal_path.write_text(
                json.dumps(
                    proposal,
                    indent=2
                ),
                encoding="utf-8"
            )

            return deployment_result

        # ----------------------------------------------------
        # VERIFICATION FAILED → AUTOMATIC ROLLBACK
        # ----------------------------------------------------

        print("Post-deployment verification: FAILED")
        print("Starting automatic rollback...")

        backup_directory = deployment_result.get(
            "backup_directory"
        )

        rollback_result = self.deployment.rollback(
            backup_directory
        )

        deployment_result["rollback"] = rollback_result

        if rollback_result.get("status") == "rolled_back":

            print("Automatic rollback: PASSED")

            deployment_result["status"] = "rolled_back"
            deployment_result["production_modified"] = False

            proposal["status"] = "rolled_back"
            proposal["production_modified"] = False
            proposal["deployment"] = deployment_result

        else:

            print("Automatic rollback: FAILED")

            deployment_result["status"] = "rollback_failed"
            deployment_result["production_modified"] = True

            proposal["status"] = "rollback_failed"
            proposal["production_modified"] = True
            proposal["deployment"] = deployment_result

        proposal_path.write_text(
            json.dumps(
                proposal,
                indent=2
            ),
            encoding="utf-8"
        )

        return deployment_result

    def development_status(self):
        return {
            "sandbox_enabled": True,
            "autonomous_development": True,
            "autonomous_testing": True,
            "production_deployment": "explicit_APPROVE_required",
            "production_backup": True,
            "post_deployment_verification": True,
            "automatic_rollback_on_verification_failure": True,
            "rollback_support": "backup_created"
        }
