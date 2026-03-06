import "dotenv/config";
import { ethers } from "ethers";
import fs from "fs";
import path from "path";

async function main() {
  const rpcUrl = process.env.AMOY_RPC_URL;
  const pk = process.env.DEPLOYER_PRIVATE_KEY;

  if (!rpcUrl) throw new Error("Missing AMOY_RPC_URL in .env");
  if (!pk) throw new Error("Missing DEPLOYER_PRIVATE_KEY in .env");

  // Load compiled artifact produced by Hardhat compile
  const artifactPath = path.join("artifacts", "contracts", "ProofRegistry.sol", "ProofRegistry.json");
  const artifact = JSON.parse(fs.readFileSync(artifactPath, "utf8"));

  const provider = new ethers.JsonRpcProvider(rpcUrl);
  const wallet = new ethers.Wallet(pk, provider);

  console.log("Deploying from:", await wallet.getAddress());

  const factory = new ethers.ContractFactory(artifact.abi, artifact.bytecode, wallet);
  const contract = await factory.deploy();
  console.log("Tx sent:", contract.deploymentTransaction().hash);

  await contract.waitForDeployment();
  const address = await contract.getAddress();

  console.log("✅ ProofRegistry deployed to:", address);
}

main().catch((e) => {
  console.error("❌ Deploy failed:", e);
  process.exit(1);
});