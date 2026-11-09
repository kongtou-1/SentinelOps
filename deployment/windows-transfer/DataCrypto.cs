using System;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;

// Portable to Windows PowerShell 5.1 / .NET Framework and PowerShell 7.
// Encrypt-then-MAC: AES-256-CBC + HMAC-SHA256, independent PBKDF2 keys.
public static class SentinelTransferCrypto {
    static readonly byte[] Magic = Encoding.ASCII.GetBytes("SENTINELX1");
    static byte[] Random(int length) {
        var data = new byte[length];
        using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(data);
        return data;
    }
    static byte[] Keys(string password, byte[] salt) {
        using (var kdf = new Rfc2898DeriveBytes(password, salt, 200000, HashAlgorithmName.SHA256))
            return kdf.GetBytes(64);
    }
    static byte[] Part(byte[] data, int start, int length) {
        var result = new byte[length]; Buffer.BlockCopy(data, start, result, 0, length); return result;
    }
    public static string Password() { return Convert.ToBase64String(Random(32)); }
    public static void Encrypt(string source, string target, string password) {
        var salt = Random(16); var iv = Random(16); var keys = Keys(password, salt);
        using (var output = File.Create(target)) {
            output.Write(Magic, 0, Magic.Length); output.Write(salt, 0, 16); output.Write(iv, 0, 16);
            using (var aes = Aes.Create()) {
                aes.Key = Part(keys, 0, 32); aes.IV = iv;
                using (var crypt = new CryptoStream(output, aes.CreateEncryptor(), CryptoStreamMode.Write, true))
                using (var input = File.OpenRead(source)) { input.CopyTo(crypt, 1048576); crypt.FlushFinalBlock(); }
            }
        }
        byte[] tag;
        using (var mac = new HMACSHA256(Part(keys, 32, 32)))
        using (var input = File.OpenRead(target)) tag = mac.ComputeHash(input);
        using (var output = new FileStream(target, FileMode.Append)) output.Write(tag, 0, tag.Length);
        Array.Clear(keys, 0, keys.Length);
    }
    public static void Decrypt(string source, string target, string password) {
        using (var input = File.OpenRead(source)) {
            if (input.Length < Magic.Length + 16 + 16 + 16 + 32) throw new InvalidDataException("Incomplete encrypted backup");
            var header = new byte[Magic.Length + 32]; input.Read(header, 0, header.Length);
            for (int i=0; i<Magic.Length; i++) if (header[i] != Magic[i]) throw new InvalidDataException("Unknown backup format");
            var keys = Keys(password, Part(header, Magic.Length, 16));
            var authenticatedLength = input.Length - 32;
            byte[] actual;
            using (var mac = new HMACSHA256(Part(keys, 32, 32))) {
                input.Position = 0; var buffer = new byte[1048576]; long remaining = authenticatedLength;
                while (remaining > 0) {
                    int n = input.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                    if (n == 0) throw new EndOfStreamException();
                    mac.TransformBlock(buffer, 0, n, buffer, 0); remaining -= n;
                }
                mac.TransformFinalBlock(new byte[0], 0, 0); actual = mac.Hash;
            }
            var expected = new byte[32]; input.Read(expected, 0, 32); int difference = 0;
            for (int i=0; i<32; i++) difference |= expected[i] ^ actual[i];
            if (difference != 0) throw new CryptographicException("Incorrect password or modified backup");
            using (var aes = Aes.Create()) {
                aes.Key = Part(keys, 0, 32); aes.IV = Part(header, Magic.Length + 16, 16);
                input.Position = header.Length;
                using (var output = File.Create(target))
                using (var crypt = new CryptoStream(output, aes.CreateDecryptor(), CryptoStreamMode.Write)) {
                    var buffer = new byte[1048576]; long remaining = authenticatedLength - header.Length;
                    while (remaining > 0) {
                        int n = input.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                        if (n == 0) throw new EndOfStreamException();
                        crypt.Write(buffer, 0, n); remaining -= n;
                    }
                    crypt.FlushFinalBlock();
                }
            }
            Array.Clear(keys, 0, keys.Length);
        }
    }
    public static void Gzip(string source, string target, bool decompress) {
        using (var input = File.OpenRead(source))
        using (var output = File.Create(target)) {
            if (decompress) using (var gz = new GZipStream(input, CompressionMode.Decompress)) gz.CopyTo(output, 1048576);
            else using (var gz = new GZipStream(output, CompressionLevel.Fastest)) input.CopyTo(gz, 1048576);
        }
    }
}
