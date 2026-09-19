using System;
using System.IO;
using NBitcoin;
using NBitcoin.Altcoins;
using Xunit;

namespace Obsidian.Tests
{
	/// <summary>
	/// Bug 1 (sync stall at first PoS block): BCA headers carry a trailing
	/// nFlags word. PoS-flagged blocks commit the masked flags into the id,
	/// everything else hashes the classic 80 bytes. Vectors are mainnet
	/// blocks 1025208 (unflagged) and 1025209 (flagged) straight from the node.
	/// </summary>
	public class BCAHeaderVectorTests
	{
		// 1025209, flags 0x80000001, id e729887bd11acbd0ea5085071fed6c90dd236ec38bcbf27dac9e41cb17ce758b
		const string FlaggedBlock =
			"0000002065b54dc1107c20990fdfc86b79df418448c331ea775959ca25000000000000007ad9f0e99128840e3da7472dfee170c06ca8fdb8d4a9101671824c79675d8633c506a66affff001d000000000100008002020000000001010000000000000000000000000000000000000000000000000000000000000000ffffffff0603b9a40f0101ffffffff02d400000000000000232102a4ffb95e96af752986a4a5c4595366c73e473b34395cf1731a2e92700652e277ac0000000000000000266a24aa21a9ed7aa3b6b40b1b0603ef22e3da098cf5e555a3d87774a76d9d2d928e2cb6f25c4401200000000000000000000000000000000000000000000000000000000000000000000000000200000001d0be240256347bb34f7a5f5f8083adc6c269d20c17c0c2df243d50705fc1e4ac0000000049483045022100906a68540593e5b3e576420c003d6aac084edc1f9f58a247b828e83d889c485f022018c5af36ab0c9f413a79f3e897cbc5306c53eeb62f941b2f8b9839b8d2847e6a41ffffffff024045efdf00000000232102a4ffb95e96af752986a4a5c4595366c73e473b34395cf1731a2e92700652e277ac6cce470c01000000232102a4ffb95e96af752986a4a5c4595366c73e473b34395cf1731a2e92700652e277ac00000000473045022100efaae6497750a84676aaccb0cb538b35e73a4b1f8586ea966bcbd96722c9482f02202e4046e2385f0348e9efc2514c1de68a3a93502a87183eb2cb04bcf1d4382873";
		// 1025208, flags 0x80000000, id 0000000000000025ca595977ea31c3488441df796bc8df0f99207c10c14db565
		const string UnflaggedBlock =
			"0000c020af0876bc0e59720e1d280a1429132a898b305d0ccddbcaa80c00000000000000c3810f78cea7aa0425f96339f8cf6e0ccb89511186bdbdac7a519028293c876eb3eda56a9fbe29195865d2e70000008001040000000001010000000000000000000000000000000000000000000000000000000000000000ffffffff1203b8a40f04b3eda56a088982a2af402001000000000003f8f3b111000000001976a914d249f2d610359c5ba9783ec835e8cbae94f8a54d88ac286bee00000000001976a9141ef6fe3f0440e2875d76aff246093cfda129665288ac0000000000000000266a24aa21a9ede2f61c3f71d1defd3fa999dfa36953755c690689799962b48bebd836974e8cf90120000000000000000000000000000000000000000000000000000000000000000000000000";

		static BlockHeader ParseHeader(string blockHex)
		{
			var bytes = Convert.FromHexString(blockHex);
			var factory = new BCAConsensusFactory();
			var header = factory.CreateBlockHeader();
			header.ReadWrite(new BitcoinStream(new MemoryStream(bytes, 0, 84, false), false));
			return header;
		}

		[Fact]
		public void FlaggedHeader_CommitsMaskedFlags()
		{
			var header = ParseHeader(FlaggedBlock);
			Assert.IsType<BCABlockHeader>(header);
			Assert.Equal(1u, ((BCABlockHeader)header).NFlags);
			Assert.Equal(new uint256("e729887bd11acbd0ea5085071fed6c90dd236ec38bcbf27dac9e41cb17ce758b"), header.GetHash());
		}

		[Fact]
		public void UnflaggedHeader_Hashes80Bytes()
		{
			var header = ParseHeader(UnflaggedBlock);
			Assert.Equal(0u, ((BCABlockHeader)header).NFlags);
			Assert.Equal(new uint256("0000000000000025ca595977ea31c3488441df796bc8df0f99207c10c14db565"), header.GetHash());
		}

		[Fact]
		public void FlaggedBlock_BodyStartsAtOffset84()
		{
			var bytes = Convert.FromHexString(FlaggedBlock);
			var block = Block.Load(bytes, new BCAConsensusFactory());
			Assert.Equal(2, block.Transactions.Count);
			Assert.Equal(new uint256("5af246e8aec40eb14c87d87bea331e472953b9969c82435a730550cf3adc58df"), block.Transactions[0].GetHash());
			Assert.Equal(new uint256("f7194f41a401f377d392a1d4ef6b11f433088acc6c3677e1aa13608f139f2e60"), block.Transactions[1].GetHash());
			Assert.Equal(new uint256("e729887bd11acbd0ea5085071fed6c90dd236ec38bcbf27dac9e41cb17ce758b"), block.GetHash());
		}

		[Fact]
		public void UnflaggedBlock_BodyParses()
		{
			var bytes = Convert.FromHexString(UnflaggedBlock);
			var block = Block.Load(bytes, new BCAConsensusFactory());
			Assert.Single(block.Transactions);
			// Single-tx block: txid equals the merkle root.
			Assert.Equal(new uint256("6e873c292890517aacbdbd86115189cb0c6ecff83963f92504aaa7ce780f81c3"), block.Transactions[0].GetHash());
			Assert.Equal(new uint256("0000000000000025ca595977ea31c3488441df796bc8df0f99207c10c14db565"), block.GetHash());
		}
	}
}
