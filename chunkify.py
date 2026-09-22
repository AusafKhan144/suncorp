import os
import pandas as pd
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import period  # noqa: E402

# ===== CONFIGURATION =====

class ChunkConfig:
    """Configuration for synchronized chunk generation"""
    
    # Master input files (one per product type)
    # Whatever Youi home CSVs this period actually dropped. A quarterly run that
    # also ships toggle/combined files gets them by dropping them in — the names
    # are read, not hardcoded, so nothing here needs editing per period.
    MASTER_FILES = {
        f"{product.lower()}_main": period.input_csv("Youi", product)
        for product in ("Buildings", "Contents")
        if period.input_csv("Youi", product)
    }
    
    # Groups of files that should have matching sample codes in chunk 1
    # No synchronized group this run — the toggle/combined files it referenced
    # are not part of a monthly home run. Restore when a quarterly run needs it.
    SYNCHRONIZED_GROUPS = []
    
    # Number of chunks to create
    NUM_CHUNKS = 10
    
    # Random state: None means a fresh random shuffle every run.
    # Set to an int (e.g. 42) if you ever need reproducible chunking.
    RANDOM_STATE = None
    
    # Whether to shuffle data before chunking
    SHUFFLE = True

# ===== UTILITY FUNCTIONS =====

def print_header():
    """Print script header"""
    print("\n" + "="*80)
    print("SYNCHRONIZED CHUNK GENERATOR (CHUNK 1 = COMMON, REST = UNIQUE)")
    print("="*80)
    print(f"Total files:      {len(ChunkConfig.MASTER_FILES)}")
    print(f"Chunks per file:  {ChunkConfig.NUM_CHUNKS}")
    print(f"Shuffle data:     {ChunkConfig.SHUFFLE}")
    print(f"Random state:     {ChunkConfig.RANDOM_STATE}")
    print("="*80)
    print("\nStrategy:")
    print("  • Chunk 1: Common samples (synchronized across all files in group)")
    print("  • Chunks 2-10: Unique samples (file-specific, may be imbalanced)")
    print("="*80 + "\n")

def validate_files() -> bool:
    """
    Validate that all master files exist
    
    Returns:
        True if all files exist, False otherwise
    """
    print("🔍 Validating master files...")
    all_exist = True
    
    for file_key, file_path in ChunkConfig.MASTER_FILES.items():
        path = Path(file_path)
        if path.exists():
            file_size = path.stat().st_size / (1024 * 1024)  # MB
            print(f"  ✓ {file_key}: {path.name} ({file_size:.2f} MB)")
        else:
            print(f"  ✗ {file_key}: NOT FOUND - {file_path}")
            all_exist = False
    
    print()
    return all_exist

def analyze_group_samples(
    group: Dict,
    master_files: Dict[str, str]
) -> Tuple[Dict[str, pd.DataFrame], Set[str], Dict[str, Set[str]]]:
    """
    Analyze sample codes in a synchronized group to find common and unique samples
    
    Args:
        group: Group configuration dictionary
        master_files: Dictionary of master file paths
        
    Returns:
        Tuple of (dataframes, common_samples, unique_samples_per_file)
    """
    group_name = group["name"]
    file_keys = group["files"]
    sample_column = group["sample_column"]
    
    print(f"\n📊 Analyzing synchronized group: {group_name}")
    print(f"   Files in group: {len(file_keys)}")
    
    dataframes = {}
    all_samples = {}
    
    # Load all files and extract sample codes
    for file_key in file_keys:
        file_path = master_files[file_key]
        print(f"   Loading {file_key}...")
        
        try:
            df = pd.read_csv(file_path)
            
            if sample_column not in df.columns:
                print(f"   ✗ ERROR: Column '{sample_column}' not found in {file_key}")
                return {}, set(), {}
            
            dataframes[file_key] = df
            all_samples[file_key] = set(df[sample_column].astype(str))
            
            print(f"     ✓ Loaded {len(df)} rows, {len(all_samples[file_key])} unique samples")
            
        except Exception as e:
            print(f"   ✗ ERROR loading {file_key}: {e}")
            return {}, set(), {}
    
    # Find common samples (intersection of all files)
    print(f"\n   🔍 Finding common samples across all files...")
    
    common_samples = set.intersection(*all_samples.values())
    
    # Find unique samples per file
    unique_samples = {}
    for file_key, samples in all_samples.items():
        unique = samples - common_samples
        unique_samples[file_key] = unique
    
    # Print analysis
    print(f"\n   📈 Sample Code Analysis:")
    print(f"      Common samples (in ALL files):  {len(common_samples)}")
    print()
    
    for file_key in file_keys:
        total = len(all_samples[file_key])
        common_count = len(common_samples)
        unique_count = len(unique_samples[file_key])
        
        print(f"      {file_key}:")
        print(f"         Total:   {total:5d} samples")
        print(f"         Common:  {common_count:5d} samples ({common_count/total*100:5.1f}%) → Chunk 1")
        print(f"         Unique:  {unique_count:5d} samples ({unique_count/total*100:5.1f}%) → Chunks 2-10")
    
    return dataframes, common_samples, unique_samples

def create_chunks_for_file(
    df: pd.DataFrame,
    common_samples: Set[str],
    unique_samples: Set[str],
    sample_column: str,
    num_chunks: int,
    file_key: str,
    shuffle: bool = True,
    random_state: Optional[int] = None
) -> List[pd.DataFrame]:
    """
    Create chunks for a single file with chunk 1 = common, rest = unique
    
    Args:
        df: Dataframe to chunk
        common_samples: Set of common sample codes (for chunk 1)
        unique_samples: Set of unique sample codes (for chunks 2-10)
        sample_column: Name of the sample code column
        num_chunks: Total number of chunks to create
        file_key: File identifier for logging
        shuffle: Whether to shuffle unique samples
        random_state: Random seed for reproducibility
        
    Returns:
        List of chunk dataframes
    """
    print(f"\n   Creating chunks for {file_key}:")
    
    chunks = []
    
    # Chunk 1: Common samples (synchronized)
    common_samples_array = np.array(list(common_samples))
    if shuffle:
        np.random.seed(random_state)
        np.random.shuffle(common_samples_array)
    
    chunk_1_df = df[df[sample_column].astype(str).isin(common_samples_array)]
    chunk_1_df = chunk_1_df.sort_values(by=sample_column).reset_index(drop=True)
    chunks.append(chunk_1_df)
    
    print(f"      Chunk 1: {len(chunk_1_df):5d} rows (common samples - synchronized)")
    
    # Chunks 2-10: Unique samples (file-specific)
    if unique_samples:
        unique_samples_array = np.array(list(unique_samples))
        if shuffle:
            np.random.seed(random_state)
            np.random.shuffle(unique_samples_array)
        
        # Split unique samples into remaining chunks
        remaining_chunks = num_chunks - 1
        unique_chunk_splits = np.array_split(unique_samples_array, remaining_chunks)
        
        for chunk_idx, chunk_samples in enumerate(unique_chunk_splits, 2):
            if len(chunk_samples) > 0:
                chunk_df = df[df[sample_column].astype(str).isin(chunk_samples)]
                chunk_df = chunk_df.sort_values(by=sample_column).reset_index(drop=True)
                chunks.append(chunk_df)
                print(f"      Chunk {chunk_idx}: {len(chunk_df):5d} rows (unique samples)")
            else:
                # Empty chunk
                empty_df = pd.DataFrame(columns=df.columns)
                chunks.append(empty_df)
                print(f"      Chunk {chunk_idx}:     0 rows (empty)")
    else:
        # No unique samples, create empty chunks
        print(f"      No unique samples, creating empty chunks 2-{num_chunks}")
        for chunk_idx in range(2, num_chunks + 1):
            empty_df = pd.DataFrame(columns=df.columns)
            chunks.append(empty_df)
            print(f"      Chunk {chunk_idx}:     0 rows (empty)")
    
    return chunks

def save_chunks(
    all_chunks: Dict[str, List[pd.DataFrame]],
    master_files: Dict[str, str]
) -> None:
    """
    Save chunk dataframes to CSV files
    
    Args:
        all_chunks: Dictionary of file_key -> list of chunk dataframes
        master_files: Dictionary of master file paths
    """
    print(f"\n💾 Saving chunks to disk...")
    
    total_files_saved = 0
    
    for file_key, chunk_list in all_chunks.items():
        master_path = Path(master_files[file_key])
        
        # Create chunks directory in the same location as master file
        chunks_dir = master_path.parent / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        
        # Get base filename without extension
        base_name = master_path.stem
        
        print(f"\n   📁 {file_key}:")
        print(f"      Output directory: {chunks_dir}")
        
        # Save each chunk
        for chunk_idx, chunk_df in enumerate(chunk_list, 1):
            chunk_filename = f"{base_name}_chunk_{chunk_idx}.csv"
            chunk_path = chunks_dir / chunk_filename
            
            chunk_df.to_csv(chunk_path, index=False)
            
            chunk_type = "SYNCHRONIZED" if chunk_idx == 1 else "unique"
            print(f"      ✓ Chunk {chunk_idx}: {chunk_filename} ({len(chunk_df):5d} rows) [{chunk_type}]")
            
            total_files_saved += 1
    
    print(f"\n   ✅ Saved {total_files_saved} chunk files total")

def verify_chunk_1_synchronization(
    file_keys: List[str],
    master_files: Dict[str, str],
    sample_column: str
) -> bool:
    """
    Verify that chunk 1 files have identical sample codes across all files
    
    Args:
        file_keys: List of file keys in the synchronized group
        master_files: Dictionary of master file paths
        sample_column: Name of sample code column
        
    Returns:
        True if chunk 1 is properly synchronized
    """
    print(f"\n🔍 Verifying chunk 1 synchronization...")
    
    chunk_1_samples = {}
    
    # Load sample codes from each file's chunk 1
    for file_key in file_keys:
        master_path = Path(master_files[file_key])
        chunks_dir = master_path.parent / "chunks"
        base_name = master_path.stem
        chunk_file = chunks_dir / f"{base_name}_chunk_1.csv"
        
        if not chunk_file.exists():
            print(f"   ✗ Chunk 1 not found for {file_key}")
            return False
        
        try:
            df = pd.read_csv(chunk_file, usecols=[sample_column])
            samples = set(df[sample_column].astype(str))
            chunk_1_samples[file_key] = samples
            print(f"   {file_key}: {len(samples)} samples in chunk 1")
        except Exception as e:
            print(f"   ✗ Error reading {file_key} chunk 1: {e}")
            return False
    
    # Verify all files have identical samples in chunk 1
    if len(chunk_1_samples) > 1:
        reference_key = list(chunk_1_samples.keys())[0]
        reference_samples = chunk_1_samples[reference_key]
        
        all_match = True
        for file_key, current_samples in chunk_1_samples.items():
            if file_key == reference_key:
                continue
            
            if reference_samples != current_samples:
                all_match = False
                missing = reference_samples - current_samples
                extra = current_samples - reference_samples
                
                print(f"   ✗ MISMATCH: {reference_key} vs {file_key}")
                print(f"      Missing in {file_key}: {len(missing)} samples")
                print(f"      Extra in {file_key}: {len(extra)} samples")
                
                if missing:
                    print(f"      Sample missing: {list(missing)[:5]}")
                if extra:
                    print(f"      Sample extra: {list(extra)[:5]}")
        
        if all_match:
            print(f"\n   ✅ Chunk 1 is synchronized across all files ({len(reference_samples)} common samples)")
            return True
        else:
            print(f"\n   ❌ Chunk 1 has synchronization issues!")
            return False
    
    return True

def split_csv_by_chunks_simple(
    file_path: str,
    num_chunks: int,
    shuffle: bool = True,
    random_state: Optional[int] = None
) -> None:
    """
    Simple split for standalone files (not part of synchronized groups)
    
    Args:
        file_path: Path to input CSV
        num_chunks: Number of chunks to create
        shuffle: Whether to shuffle rows before splitting
        random_state: Random seed for reproducibility
    """
    path = Path(file_path)
    
    print(f"\n📄 Processing standalone file: {path.name}")
    
    # Create chunks directory
    chunks_dir = path.parent / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    
    # Load dataframe
    df = pd.read_csv(file_path)
    print(f"   Loaded {len(df)} rows")
    
    # Shuffle if requested
    if shuffle:
        df = df.sample(frac=1, random_state=random_state).reset_index(drop=True)
        print(f"   Shuffled with random_state={random_state}")
    
    # Split into chunks
    total_rows = len(df)
    indices_split = np.array_split(np.arange(total_rows), num_chunks)
    
    base_name = path.stem
    
    print(f"   Creating {num_chunks} chunks:")
    for i, idx in enumerate(indices_split, 1):
        chunk = df.iloc[idx]
        chunk_file = chunks_dir / f"{base_name}_chunk_{i}.csv"
        chunk.to_csv(chunk_file, index=False)
        print(f"     ✓ Chunk {i}: {chunk_file.name} ({len(chunk)} rows)")
    
    print(f"   ✅ Saved {num_chunks} chunks in {chunks_dir}")

def print_chunk_summary(
    group: Dict,
    common_samples: Set[str],
    unique_samples: Dict[str, Set[str]]
) -> None:
    """
    Print detailed summary of chunk distribution
    
    Args:
        group: Group configuration
        common_samples: Set of common sample codes
        unique_samples: Dictionary of unique samples per file
    """
    print(f"\n" + "="*80)
    print(f"CHUNK SUMMARY FOR GROUP: {group['name']}")
    print("="*80)
    
    file_keys = group["files"]
    
    print(f"\n📊 Chunk 1 (Synchronized - Common Samples):")
    print(f"   Total common samples:  {len(common_samples)}")
    print(f"   Present in:            ALL {len(file_keys)} files")
    print(f"   Status:                ✅ SYNCHRONIZED")
    
    print(f"\n📊 Chunks 2-10 (Unique Samples - File-Specific):")
    for file_key in file_keys:
        unique_count = len(unique_samples.get(file_key, set()))
        print(f"   {file_key}:")
        print(f"      Unique samples:  {unique_count}")
        print(f"      Per chunk avg:   ~{unique_count // 9}")
        print(f"      Status:          File-specific (may be imbalanced)")
    
    print(f"\n📦 Chunk Structure:")
    print(f"   Chunk 1:   Common samples (synchronized across all files)")
    print(f"   Chunk 2:   Unique samples (file-specific, imbalanced)")
    print(f"   Chunk 3:   Unique samples (file-specific, imbalanced)")
    print(f"   ...")
    print(f"   Chunk 10:  Unique samples (file-specific, imbalanced)")
    
    print("="*80)

# ===== MAIN EXECUTION =====

def main():
    """Main execution function"""
    try:
        print_header()
        
        # Validate all files exist
        if not validate_files():
            print("❌ Some master files are missing. Please check file paths.")
            sys.exit(1)
        
        print("✅ All master files validated\n")
        
        # Process synchronized groups
        for group in ChunkConfig.SYNCHRONIZED_GROUPS:
            print("\n" + "="*80)
            print(f"PROCESSING SYNCHRONIZED GROUP: {group['name']}")
            print("="*80)
            
            # Analyze group to find common and unique samples
            dataframes, common_samples, unique_samples = analyze_group_samples(
                group, ChunkConfig.MASTER_FILES
            )
            
            if not dataframes:
                print(f"\n❌ Failed to load group '{group['name']}'")
                continue
            
            if not common_samples:
                print(f"\n⚠️  No common samples found in group '{group['name']}'")
                print("   Each file will be chunked independently.")
                
                # Process each file independently
                for file_key in group["files"]:
                    split_csv_by_chunks_simple(
                        file_path=ChunkConfig.MASTER_FILES[file_key],
                        num_chunks=ChunkConfig.NUM_CHUNKS,
                        shuffle=ChunkConfig.SHUFFLE,
                        random_state=ChunkConfig.RANDOM_STATE
                    )
                continue
            
            # Create chunks for each file
            print(f"\n🔀 Creating chunks for all files...")
            all_chunks = {}
            
            for file_key in group["files"]:
                chunks = create_chunks_for_file(
                    df=dataframes[file_key],
                    common_samples=common_samples,
                    unique_samples=unique_samples[file_key],
                    sample_column=group["sample_column"],
                    num_chunks=ChunkConfig.NUM_CHUNKS,
                    file_key=file_key,
                    shuffle=ChunkConfig.SHUFFLE,
                    random_state=ChunkConfig.RANDOM_STATE
                )
                all_chunks[file_key] = chunks
            
            # Save all chunks
            save_chunks(
                all_chunks=all_chunks,
                master_files=ChunkConfig.MASTER_FILES
            )
            
            # Verify chunk 1 synchronization
            if verify_chunk_1_synchronization(
                file_keys=group["files"],
                master_files=ChunkConfig.MASTER_FILES,
                sample_column=group["sample_column"]
            ):
                print(f"\n✅ Group '{group['name']}' chunk 1 verified successfully!")
            else:
                print(f"\n⚠️  Group '{group['name']}' chunk 1 has verification issues!")
            
            # Print summary
            print_chunk_summary(
                group=group,
                common_samples=common_samples,
                unique_samples=unique_samples
            )
        
        # Process standalone files (not in any synchronized group)
        all_grouped_files = set()
        for group in ChunkConfig.SYNCHRONIZED_GROUPS:
            all_grouped_files.update(group["files"])
        
        standalone_files = {
            key: path for key, path in ChunkConfig.MASTER_FILES.items()
            if key not in all_grouped_files
        }
        
        if standalone_files:
            print("\n" + "="*80)
            print("PROCESSING STANDALONE FILES")
            print("="*80)
            
            for file_key, file_path in standalone_files.items():
                split_csv_by_chunks_simple(
                    file_path=file_path,
                    num_chunks=ChunkConfig.NUM_CHUNKS,
                    shuffle=ChunkConfig.SHUFFLE,
                    random_state=ChunkConfig.RANDOM_STATE
                )
        
        # Final summary
        print("\n" + "="*80)
        print("CHUNK GENERATION COMPLETE")
        print("="*80)
        print(f"✅ Successfully generated {ChunkConfig.NUM_CHUNKS} chunks for each file")
        print(f"\n📋 Chunk Structure:")
        print(f"   • Chunk 1:   Common samples (synchronized across all group files)")
        print(f"   • Chunks 2-10: Unique samples (file-specific, may be imbalanced)")
        print(f"\n💡 Usage:")
        print(f"   • Process chunk 1 in parallel across all files for synchronized data")
        print(f"   • Process chunks 2-10 independently per file for unique data")
        print("="*80 + "\n")
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Operation interrupted by user (Ctrl+C)")
        sys.exit(130)
        
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()