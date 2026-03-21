package com.example.test

import android.os.Bundle
import android.util.Log
import androidx.appcompat.app.AppCompatActivity
import androidx.databinding.DataBindingUtil
import com.example.test.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {

    private var binding: ActivityMainBinding? = null
    
    companion object {
        private const val TAG = "MainActivity"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        try {
            // Safe initialization with null checks
            binding = DataBindingUtil.setContentView(this, R.layout.activity_main)
            
            // Verify binding was successful
            binding?.let { safeBinding ->
                initializeViews(safeBinding)
            } ?: run {
                Log.e(TAG, "Failed to initialize data binding")
                finish()
                return
            }
            
            // Safe intent extras handling
            handleIntentExtras()
            
        } catch (e: Exception) {
            Log.e(TAG, "Error in onCreate: ${e.message}", e)
            // Graceful fallback
            try {
                setContentView(R.layout.activity_main)
            } catch (layoutException: Exception) {
                Log.e(TAG, "Fatal error: Cannot set content view", layoutException)
                finish()
            }
        }
    }
    
    private fun initializeViews(binding: ActivityMainBinding) {
        try {
            // Safe view initialization
            binding.apply {
                // Add your view initialization here
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error initializing views: ${e.message}", e)
        }
    }
    
    private fun handleIntentExtras() {
        try {
            intent?.extras?.let { extras ->
                // Safe handling of intent extras
                // Add your intent handling logic here
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error handling intent extras: ${e.message}", e)
        }
    }
    
    override fun onDestroy() {
        try {
            binding = null
            super.onDestroy()
        } catch (e: Exception) {
            Log.e(TAG, "Error in onDestroy: ${e.message}", e)
        }
    }
}