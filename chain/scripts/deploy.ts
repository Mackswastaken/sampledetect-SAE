import { ethers } from "hardhat";

async function main() {
  const ProofRegistry = await ethers.getContractFactory("ProofRegistry");
  const contract = await ProofRegistry.deploy();
  await contract.waitForDeployment();

  console.log("ProofRegistry deployed to:", await contract.getAddress());
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});