using NBitcoin;
using NBitcoin.DataEncoders;
using NBitcoin.Protocol;
using System;

namespace NBitcoin.Altcoins
{
	/// <summary>
	/// Bitcoin Atom (BCA) network set for NBXplorer.
	/// Params taken from the BCA Core chainparams (mainnet):
	/// P2PKH=23, P2SH=10, WIF=128, P2P=7333, RPC=7332, magic=0x4fc11de8,
	/// bech32 "bca", genesis = BTC genesis (shared pre-fork history).
	/// Note: BCA's post-fork hybrid PoS blocks use 84-byte headers which
	/// NBitcoin cannot parse; NBXplorer tracks PoW blocks and RPC data.
	/// </summary>
	public class BitcoinAtom : NetworkSetBase
	{
		public static BitcoinAtom Instance { get; } = new BitcoinAtom();

		public override string CryptoCode => "BCA";

		private BitcoinAtom()
		{

		}

		protected override void PostInit()
		{
			RegisterDefaultCookiePath("BitcoinAtom", new FolderName() { TestnetFolder = "testnet3" });
		}

		protected override NetworkBuilder CreateMainnet()
		{
			var bech32 = Encoders.Bech32("bca");
			NetworkBuilder builder = new NetworkBuilder();
			builder.SetConsensus(new Consensus()
			{
				SubsidyHalvingInterval = 210000,
				MajorityEnforceBlockUpgrade = 750,
				MajorityRejectBlockOutdated = 950,
				MajorityWindow = 1000,
				BIP34Hash = new uint256("fa09d204a83a768ed5a7c8d441fa62f2043abf420cff1226c7b4329aeb9d51cf"),
				PowLimit = new Target(new uint256("00000000ffffffffffffffffffffffffffffffffffffffffffffffffffffffff")),
				PowTargetTimespan = TimeSpan.FromSeconds(14 * 24 * 60 * 60),
				PowTargetSpacing = TimeSpan.FromSeconds(10 * 60),
				PowAllowMinDifficultyBlocks = false,
				PowNoRetargeting = false,
				RuleChangeActivationThreshold = 1916,
				MinerConfirmationWindow = 2016,
				CoinbaseMaturity = 100,
				ConsensusFactory = new ConsensusFactory(),
				SupportSegwit = true
			})
			.SetBase58Bytes(Base58Type.PUBKEY_ADDRESS, new byte[] { 23 })
			.SetBase58Bytes(Base58Type.SCRIPT_ADDRESS, new byte[] { 10 })
			.SetBase58Bytes(Base58Type.SECRET_KEY, new byte[] { 128 })
			.SetBase58Bytes(Base58Type.EXT_PUBLIC_KEY, new byte[] { 0x04, 0x88, 0xB2, 0x1E })
			.SetBase58Bytes(Base58Type.EXT_SECRET_KEY, new byte[] { 0x04, 0x88, 0xAD, 0xE4 })
			.SetBech32(Bech32Type.WITNESS_PUBKEY_ADDRESS, bech32)
			.SetBech32(Bech32Type.WITNESS_SCRIPT_ADDRESS, bech32)
			.SetMagic(0xe81dc14f)
			.SetPort(7333)
			.SetRPCPort(7332)
			.SetName("bca-main")
			.AddAlias("bca-mainnet")
			.AddAlias("bitcoinatom-mainnet")
			.AddAlias("bitcoinatom-main")
			.SetGenesis("0100000000000000000000000000000000000000000000000000000000000000000000003ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4a29ab5f49ffff001d1dac2b7c0101000000010000000000000000000000000000000000000000000000000000000000000000ffffffff4d04ffff001d0104455468652054696d65732030332f4a616e2f32303039204368616e63656c6c6f72206f6e206272696e6b206f66207365636f6e64206261696c6f757420666f722062616e6b73ffffffff0100f2052a01000000434104678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61deb649f6bc3f4cef38c4f35504e51ec112f5f6daa200000000010000000000000000000000000000000000000000000000000000000000000000000000000000ffff");
			return builder;
		}

		protected override NetworkBuilder CreateTestnet()
		{
			var bech32 = Encoders.Bech32("tbca");
			NetworkBuilder builder = new NetworkBuilder();
			builder.SetConsensus(new Consensus()
			{
				SubsidyHalvingInterval = 210000,
				MajorityEnforceBlockUpgrade = 51,
				MajorityRejectBlockOutdated = 75,
				MajorityWindow = 100,
				PowLimit = new Target(new uint256("00000000ffffffffffffffffffffffffffffffffffffffffffffffffffffffff")),
				PowTargetTimespan = TimeSpan.FromSeconds(14 * 24 * 60 * 60),
				PowTargetSpacing = TimeSpan.FromSeconds(10 * 60),
				PowAllowMinDifficultyBlocks = true,
				PowNoRetargeting = false,
				RuleChangeActivationThreshold = 1512,
				MinerConfirmationWindow = 2016,
				CoinbaseMaturity = 100,
				ConsensusFactory = new ConsensusFactory(),
				SupportSegwit = true
			})
			.SetBase58Bytes(Base58Type.PUBKEY_ADDRESS, new byte[] { 111 })
			.SetBase58Bytes(Base58Type.SCRIPT_ADDRESS, new byte[] { 196 })
			.SetBase58Bytes(Base58Type.SECRET_KEY, new byte[] { 239 })
			.SetBase58Bytes(Base58Type.EXT_PUBLIC_KEY, new byte[] { 0x04, 0x35, 0x87, 0xCF })
			.SetBase58Bytes(Base58Type.EXT_SECRET_KEY, new byte[] { 0x04, 0x35, 0x83, 0x94 })
			.SetBech32(Bech32Type.WITNESS_PUBKEY_ADDRESS, bech32)
			.SetBech32(Bech32Type.WITNESS_SCRIPT_ADDRESS, bech32)
			.SetMagic(0xd63f8ea6)
			.SetPort(17333)
			.SetRPCPort(17332)
			.SetName("bca-test")
			.AddAlias("bca-testnet")
			.AddAlias("bitcoinatom-testnet")
			.AddAlias("bitcoinatom-test");
			return builder;
		}

		protected override NetworkBuilder CreateRegtest()
		{
			var bech32 = Encoders.Bech32("bcart");
			NetworkBuilder builder = new NetworkBuilder();
			builder.SetConsensus(new Consensus()
			{
				SubsidyHalvingInterval = 150,
				MajorityEnforceBlockUpgrade = 750,
				MajorityRejectBlockOutdated = 950,
				MajorityWindow = 1000,
				PowLimit = new Target(new uint256("7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff")),
				PowTargetTimespan = TimeSpan.FromSeconds(14 * 24 * 60 * 60),
				PowTargetSpacing = TimeSpan.FromSeconds(10 * 60),
				PowAllowMinDifficultyBlocks = true,
				MinimumChainWork = uint256.Zero,
				PowNoRetargeting = true,
				RuleChangeActivationThreshold = 108,
				MinerConfirmationWindow = 144,
				CoinbaseMaturity = 100,
				ConsensusFactory = new ConsensusFactory(),
				SupportSegwit = true
			})
			.SetBase58Bytes(Base58Type.PUBKEY_ADDRESS, new byte[] { 111 })
			.SetBase58Bytes(Base58Type.SCRIPT_ADDRESS, new byte[] { 196 })
			.SetBase58Bytes(Base58Type.SECRET_KEY, new byte[] { 239 })
			.SetBase58Bytes(Base58Type.EXT_PUBLIC_KEY, new byte[] { 0x04, 0x35, 0x87, 0xCF })
			.SetBase58Bytes(Base58Type.EXT_SECRET_KEY, new byte[] { 0x04, 0x35, 0x83, 0x94 })
			.SetBech32(Bech32Type.WITNESS_PUBKEY_ADDRESS, bech32)
			.SetBech32(Bech32Type.WITNESS_SCRIPT_ADDRESS, bech32)
			.SetMagic(0x4a1fd7ca)
			.SetPort(18444)
			.SetRPCPort(18443)
			.SetName("bca-reg")
			.AddAlias("bca-regtest")
			.AddAlias("bitcoinatom-regtest")
			.AddAlias("bitcoinatom-reg");
			return builder;
		}
	}
}

namespace NBXplorer
{
	public partial class NBXplorerNetworkProvider
	{
		private void InitBitcoinAtom(NetworkType networkType)
		{
			Add(new NBXplorerNetwork(NBitcoin.Altcoins.BitcoinAtom.Instance, networkType)
			{
				// BCA daemon is 0.16.2-based (protocol 70020)
				MinRPCVersion = 150000
			});
		}

		public NBXplorerNetwork GetBCA()
		{
			return GetFromCryptoCode(NBitcoin.Altcoins.BitcoinAtom.Instance.CryptoCode);
		}
	}
}
